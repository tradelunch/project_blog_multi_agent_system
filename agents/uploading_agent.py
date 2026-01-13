# agents/04_uploading_agent.py
"""
04. UploadingAgent - S3 이미지 업로드 및 RDS 데이터 저장 에이전트

외부 시스템(S3, RDS)과 통신하여 데이터를 저장합니다.

역할:
- 썸네일 S3 업로드 (우선)
- 본문 이미지 S3 업로드
- 마크다운 내 URL 교체
- Post 데이터 RDS 저장 (schema/posts.schema.sql 스키마 준수)
- 스키마 검증 (PostSchema, FileSchema)
"""

import asyncio
import config
from typing import Dict, Any, List, Optional
from pathlib import Path
from pydantic import ValidationError
from .base import BaseAgent
from schema import (
    PostSchema,
    FileSchema,
    PostStatusEnum,
    generate_slug_from_title,
    UploadPayload,
    ArticleMetadata,
    FileInfo,
    CategoryInfo,
)


class UploadingAgent(BaseAgent):
    """
    S3 이미지 업로드 및 RDS 데이터 저장 에이전트

    작업:
    1. 이미지를 S3에 업로드
    2. 문서 데이터를 RDS에 저장
    3. URL 매핑 및 반환
    """

    def __init__(self, s3_bucket: str = "my-blog-bucket"):
        super().__init__(name="UploadingAgent", description="S3 image upload and RDS data storage")
        self.s3_bucket = s3_bucket
        self._db_session = None  # Will be set per operation

    async def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        작업 실행

        Expected task actions:
            - upload_images: 이미지들을 S3에 업로드
            - save_article: 문서를 RDS에 저장
            - full_upload: 이미지 업로드 + 문서 저장
        """
        action = task.get("action")
        data = task.get("data", {})

        try:
            if action == "upload_images":
                return await self._upload_images(data.get("images", []))

            elif action == "save_article":
                return await self._save_article(data)

            elif action == "full_upload":
                # Validate thumbnail is present
                thumbnail = data.get("thumbnail")
                if not thumbnail:
                    title = data.get("title", "Unknown")
                    return {
                        "success": False,
                        "error": f"Thumbnail image is required for post '{title}'. "
                        f"Please add an image with the same name as the markdown file "
                        f"(e.g., my-post.md → my-post.png) or use ![thumbnail](path) in content.",
                        "agent": self.name,
                    }

                # S3 path context: [user_id]/[category]/.../[slug]/
                upload_context = {
                    "user_id": data.get("user_id", config.DEFAULT_USER_ID),
                    "categories": data.get("categories", []),
                    "slug": data.get("slug", "untitled"),
                }

                # 썸네일 업로드
                thumbnail_url = None

                if thumbnail:
                    self._log("Uploading thumbnail...")
                    upload_result = await self._upload_images(
                        [], thumbnail, upload_context, is_thumbnail=True
                    )
                    if not upload_result["success"]:
                        return upload_result
                    thumbnail_url = upload_result["data"]["thumbnail_url"]
                    # Store thumbnail s3 info
                    if upload_result["data"].get("thumbnail_data"):
                        data["thumbnail"] = upload_result["data"]["thumbnail_data"]

                # 이미지 업로드
                images = data.get("images", [])
                if images:
                    self._log(f"Uploading {len(images)} image(s) to S3...")
                    upload_result = await self._upload_images(images, None, upload_context)
                    if not upload_result["success"]:
                        return upload_result

                    # S3 URL을 데이터에 업데이트
                    data["image_urls"] = upload_result["data"]["s3_urls"]
                    data["images"] = upload_result["data"]["images"]
                else:
                    data["image_urls"] = []

                # 썸네일 URL 추가
                if thumbnail_url:
                    data["thumbnail_url"] = thumbnail_url

                # 문서 저장
                self._log("Saving article to database...")
                return await self._save_article(data)

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            return {"success": False, "error": str(e), "agent": self.name}

    async def _upload_images(
        self,
        images: List[Dict[str, str]],
        thumbnail: Dict[str, str] = None,
        context: Dict[str, Any] = None,
        is_thumbnail: bool = False,
    ) -> Dict[str, Any]:
        """
        이미지들을 S3에 업로드 (썸네일 우선 처리)

        S3 Path Structure (slug-based naming):
            [user_id]/[category]/.../[slug]/[slug].ext           <- thumbnail
            [user_id]/[category]/.../[slug]/[slug]-1.ext         <- image 1
            [user_id]/[category]/.../[slug]/[slug]-2.ext         <- image 2

        Args:
            images: [{"local_path": "...", "alt": "...", "s3_url": None}, ...]
            thumbnail: {"local_path": "...", "alt": "...", "s3_url": None} (옵션)
            context: {"user_id": int, "categories": [str], "slug": str}
            is_thumbnail: Whether uploading thumbnail only

        Returns:
            {
                "success": bool,
                "data": {
                    "thumbnail_url": str,
                    "thumbnail_data": {...},
                    "images": [...],  # 업데이트된 이미지 정보
                    "s3_urls": [...]  # S3 URLs 목록
                }
            }
        """
        context = context or {}
        user_id = context.get("user_id", config.DEFAULT_USER_ID)
        categories = context.get("categories", [])
        slug = context.get("slug", "untitled")

        s3_urls = []
        updated_images = []
        thumbnail_url = None
        thumbnail_data = None

        # Build S3 path prefix: [user_id]/[category]/[subcategory]/.../[slug]/
        path_parts = [str(user_id)]
        path_parts.extend(categories)
        path_parts.append(slug)
        s3_prefix = "/".join(path_parts)

        # 1. 썸네일 먼저 업로드 (있는 경우)
        if thumbnail:
            local_path = (
                thumbnail.get("local_path") if isinstance(thumbnail, dict) else str(thumbnail)
            )
            original_filename = Path(local_path).name
            ext = Path(local_path).suffix.lstrip(".")  # e.g., "png"

            # Resize thumbnail to OG dimensions (1200x630) with transparent letterboxing
            from agents.image_processing_agent import ImageProcessingAgent
            img_processor = ImageProcessingAgent()
            local_path = img_processor.resize_for_og(local_path)
            self._log(f"Resized thumbnail for OG: {original_filename}")

            # Thumbnail uses slug-based name: [prefix]/[slug].ext
            stored_name = f"{slug}.{ext}"
            s3_key = f"{s3_prefix}/{stored_name}"

            # Upload to S3 using db/s3 module
            thumbnail_url = await self._upload_file_to_s3(
                local_path=local_path,
                user_id=user_id,
                folder_path="/".join(categories) if categories else "",
                slug=slug,
                ext=ext,
                is_thumbnail=True,
            )

            thumbnail_data = {
                **(thumbnail if isinstance(thumbnail, dict) else {"local_path": thumbnail}),
                "original_filename": original_filename,
                "stored_name": stored_name,
                "s3_url": thumbnail_url,
                "s3_key": s3_key,
                "is_thumbnail": True,
            }

            self._log(f"Thumbnail uploaded: {original_filename} -> {s3_key}")

        # 2. 나머지 이미지들 업로드
        if not images:
            return {
                "success": True,
                "data": {
                    "thumbnail_url": thumbnail_url,
                    "thumbnail_data": thumbnail_data,
                    "images": [],
                    "s3_urls": [],
                },
            }

        for idx, img in enumerate(images, start=1):
            local_path = img["local_path"]
            original_filename = Path(local_path).name
            ext = Path(local_path).suffix.lstrip(".")  # e.g., "png"

            # Image uses slug-based name: [prefix]/[slug]-[index].ext
            stored_name = f"{slug}-{idx}.{ext}"
            s3_key = f"{s3_prefix}/{stored_name}"

            # Upload to S3 using db/s3 module
            s3_url = await self._upload_file_to_s3(
                local_path=local_path,
                user_id=user_id,
                folder_path="/".join(categories) if categories else "",
                slug=f"{slug}-{idx}",
                ext=ext,
                is_thumbnail=False,
            )

            s3_urls.append(s3_url)
            updated_images.append(
                {
                    **img,
                    "original_filename": original_filename,
                    "stored_name": stored_name,
                    "s3_url": s3_url,
                    "s3_key": s3_key,
                }
            )

            self._log(f"Uploaded: {original_filename} -> {stored_name}")

        return {
            "success": True,
            "data": {
                "thumbnail_url": thumbnail_url,
                "thumbnail_data": thumbnail_data,
                "images": updated_images,
                "s3_urls": s3_urls,
            },
            "agent": self.name,
        }

    async def _upload_file_to_s3(
        self,
        local_path: str,
        user_id: int,
        folder_path: str,
        slug: str,
        ext: str,
        is_thumbnail: bool = False,
    ) -> str:
        """
        Upload a file to S3 using db/s3 module.

        Args:
            local_path: Local file path
            user_id: User ID
            folder_path: Folder path in S3 (category hierarchy)
            slug: File slug
            ext: File extension (without dot)
            is_thumbnail: Whether this is a thumbnail

        Returns:
            CDN URL for the uploaded file
        """
        from db.s3 import FileMetadata, async_upload_file_s3
        from utils.snowflake import generate_id

        # Read file content
        file_path = Path(local_path)
        if not file_path.exists():
            # Return simulated URL if file doesn't exist (for testing)
            s3_key = f"{user_id}/{folder_path}/{slug}/{slug}.{ext}"
            return f"{config.CDN_ASSET_POSTS}/{s3_key}"

        with open(file_path, "rb") as f:
            buffer = f.read()

        # Create file metadata
        meta = FileMetadata(
            id=generate_id(),
            user_id=user_id,
            folder_path=folder_path,
            slug=slug,
            filename=file_path.name,
            ext=ext,
            buffer=buffer,
            content_type=self._get_content_type(ext),
            is_thumbnail=is_thumbnail,
        )

        # Upload to S3
        result = await async_upload_file_s3(meta)

        return result.stored_uri

    async def _save_article(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        문서를 RDS에 저장 (schema/posts.schema.sql 준수)

        Args:
            data: {
                "title": str,
                "content": str,
                "slug": str,
                "user_id": int,  # Required by PostSchema
                "description": str (optional, summary),
                "tags": [str],
                "categories": [str],  # Full hierarchy from folder path
                "category_id": int (optional),
                "image_urls": [str],
                "images": [{"local_path": str, "s3_url": str}],
                ...
            }

        Returns:
            {
                "success": bool,
                "data": {
                    "article_id": int,
                    "slug": str,
                    "published_url": str,
                    "categories": [...],
                    "category_ids": [...],
                    "images": [...] with CDN URLs
                }
            }
        """
        # 0. Replace local image paths with CDN URLs in content
        content = data.get("content", "")
        images = data.get("images", [])
        thumbnail = data.get("thumbnail")

        if images or thumbnail:
            content, images = self._replace_content_urls(content, images, thumbnail)
            data["content"] = content
            self._log(f"✓ Replaced image URLs with CDN paths")

        # Validate status field
        status_value = data.get("status", PostStatusEnum.PUBLIC)
        if status_value not in ["public", "private", "follower"]:
            self._log(f"Invalid status '{status_value}', using 'public'", "warning")
            status_value = PostStatusEnum.PUBLIC

        # Validate against PostSchema (matches schema/posts.schema.sql)
        try:
            post_data = {
                "user_id": data.get("user_id", config.DEFAULT_USER_ID),
                "slug": data.get("slug") or generate_slug_from_title(data.get("title", "untitled")),
                "title": data.get("title"),
                "description": data.get("summary") or data.get("description"),
                "content": content,
                "status": status_value,
                "category_id": data.get("category_id"),
                "group_id": data.get("group_id"),
                "level": data.get("level", 0),
                "parent_id": data.get("parent_id"),
                "priority": data.get("priority", 100),
            }
            post = PostSchema(**post_data)
            self._log(f"✓ Schema validation passed for post: {post.title}")
        except ValidationError as e:
            return {
                "success": False,
                "error": f"Schema validation failed: {e}",
            }

        # Use SINGLE database session for all operations
        from db import get_db_session, PostRepository, CategoryRepository, TagRepository

        categories = data.get("categories", [])
        category_ids = []
        deepest_category_id = None
        category_infos = []

        async with get_db_session() as session:
            # 1. Resolve category hierarchy (within same session)
            if categories:
                self._log(f"Resolving category hierarchy: {' > '.join(categories)}")
                category_ids, deepest_category_id, category_infos = (
                    await self._resolve_category_hierarchy_with_session(
                        session, categories, user_id=data.get("user_id", config.DEFAULT_USER_ID)
                    )
                )
                self._log(
                    f"✓ Resolved {len(category_ids)} categories, deepest ID: {deepest_category_id}"
                )

            # 2. Create post using upsert
            post_repo = PostRepository(session)
            
            # Build SEO fields
            thumbnail_data = data.get("thumbnail", {})
            og_image_url = thumbnail_data.get("s3_url") if isinstance(thumbnail_data, dict) else None
            meta_title = data.get("title", "")[:70]
            meta_description = (data.get("summary") or data.get("description", ""))[:170] if data.get("summary") or data.get("description") else None
            og_image_alt = f"{data.get('title', 'Article')} thumbnail"
            
            article_id = await post_repo.upsert_post(
                user_id=data.get("user_id", config.DEFAULT_USER_ID),
                title=data.get("title"),
                slug=data.get("slug") or generate_slug_from_title(data.get("title", "untitled")),
                content=content,
                description=data.get("summary") or data.get("description"),
                category_id=deepest_category_id,
                status=status_value,
                # SEO fields
                meta_title=meta_title,
                meta_description=meta_description,
                og_image_url=og_image_url,
                og_image_alt=og_image_alt,
            )

            # 3. Link post to all categories in hierarchy
            if category_ids:
                await self._link_post_to_categories(session, article_id, category_ids)

            # 4. Process tags: upsert to tags table and link to post_tags
            tags = data.get("tags", [])
            if tags:
                self._log(f"Processing {len(tags)} tag(s)...")
                tag_repo = TagRepository(session)
                linked_tags = await tag_repo.upsert_and_link_tags(article_id, tags)
                self._log(f"✓ Linked tags: {', '.join(linked_tags)}")

            # 5. Save file records
            user_id = data.get("user_id", config.DEFAULT_USER_ID)
            await self._save_file_records(session, article_id, images, thumbnail, user_id)

            # 6. Commit all changes in single transaction
            await session.commit()
            self._log(f"Post saved with ID: {article_id}")

        slug = data["slug"]
        # Use username from data, fallback to author (slugified), then default
        username = data.get("username")
        if not username:
            author = data.get("author", config.DEFAULT_USERNAME)
            # Slugify author name for URL (lowercase, replace spaces with hyphen)
            username = author.lower().replace(" ", "-") if author else config.DEFAULT_USERNAME
        published_url = f"{config.BLOG_BASE_URL}/blog/@{username}/{slug}"

        self._log(f"Article saved with ID: {article_id}")

        # Build upload payload for logging/debugging
        upload_payload = self._build_upload_payload(data, category_ids, category_infos)
        upload_payload_dict = upload_payload.model_dump(exclude_none=True)

        return {
            "success": True,
            "data": {
                "article_id": article_id,
                "title": data["title"],
                "slug": slug,
                "categories": categories,  # Full hierarchy as list
                "category_ids": category_ids,  # All category IDs
                "category_id": deepest_category_id,  # Primary (deepest) category
                "published_url": published_url,
                "image_count": len(images) + (1 if thumbnail else 0),
                "images": images,  # Include updated images with CDN URLs
                "upload_payload": upload_payload_dict,  # Full payload for logging
            },
            "agent": self.name,
        }

    def _replace_content_urls(
        self, content: str, images: List[Dict], thumbnail: Dict = None
    ) -> tuple[str, List[Dict]]:
        """
        Replace local image paths in markdown content with CDN URLs.

        Uses stored_name (slug-based) for CDN URLs instead of original filename.

        Args:
            content: Markdown content with ![alt](local/path) syntax
            images: List of image dicts with local_path, s3_url, stored_name
            thumbnail: Optional thumbnail dict

        Returns:
            Tuple of (updated_content, updated_images with cdn_url)
        """
        import re

        updated_images = []
        
        # Combine thumbnail and images for processing
        all_items = []
        if thumbnail and isinstance(thumbnail, dict):
            all_items.append(thumbnail)
        all_items.extend(images)

        for img in all_items:
            local_path = img.get("local_path", "")
            s3_url = img.get("s3_url", "")
            stored_name = img.get("stored_name", "")

            if not local_path or not s3_url:
                if img not in (thumbnail if isinstance(thumbnail, dict) else []):
                    updated_images.append(img)
                continue

            # Use s3_url directly (already contains full CDN path with slug-based name)
            cdn_url = s3_url

            # Find and replace in content - match original filename in markdown
            original_filename = img.get("original_filename", Path(local_path).name)
            # Pattern: ![alt](anything/original_filename) or ![alt](original_filename)
            pattern = rf"!\[([^\]]*)\]\([^)]*{re.escape(original_filename)}\)"
            replacement = f"![\\1]({cdn_url})"
            content = re.sub(pattern, replacement, content)

            # Update image dict with CDN URL (skip thumbnail from images list)
            if img is not thumbnail:
                updated_images.append(
                    {
                        **img,
                        "cdn_url": cdn_url,
                    }
                )

        return content, updated_images

    async def _save_file_records(
        self, session, post_id: int, images: List[Dict], thumbnail: Dict, user_id: int
    ) -> List[int]:
        """
        Save image/file records to files table.

        Args:
            session: Database session
            post_id: The post ID to associate files with
            images: List of image dicts with local_path, s3_url, cdn_url
            thumbnail: Optional thumbnail dict
            user_id: Owner user ID

        Returns:
            List of created file IDs
        """
        from config import CDN_ASSET_POSTS
        from db.repositories.file import FileRepository

        file_repo = FileRepository(session)
        file_ids = []
        all_files = []

        # Add thumbnail first if exists
        if thumbnail:
            if isinstance(thumbnail, dict):
                all_files.append(
                    {
                        **thumbnail,
                        "is_thumbnail": True,
                    }
                )
            else:
                all_files.append(
                    {
                        "local_path": str(thumbnail),
                        "is_thumbnail": True,
                    }
                )

        # Add regular images
        for img in images:
            all_files.append(
                {
                    **img,
                    "is_thumbnail": False,
                }
            )

        for file_data in all_files:
            local_path = file_data.get("local_path", "")
            s3_url = file_data.get("s3_url", "")
            is_thumbnail = file_data.get("is_thumbnail", False)

            if not local_path:
                continue

            # Use original_filename and stored_name from upload data (slug-based)
            original_filename = file_data.get("original_filename", Path(local_path).name)
            stored_name = file_data.get("stored_name", original_filename)
            s3_key = file_data.get("s3_key")  # S3 key path
            ext = Path(local_path).suffix.lstrip(".")

            # Determine stored URI (CDN URL) - use stored_name (slug-based)
            if s3_url:
                stored_uri = s3_url  # Already contains the full CDN URL
            else:
                stored_uri = f"{CDN_ASSET_POSTS}/images/{stored_name}"

            # Get file size (if file exists locally)
            file_size = None
            if Path(local_path).exists():
                file_size = Path(local_path).stat().st_size

            try:
                # Use FileRepository to upsert file record
                file_id = await file_repo.upsert_file_record(
                    user_id=user_id,
                    post_id=int(post_id),
                    original_filename=original_filename,
                    stored_name=stored_name,
                    stored_uri=stored_uri, # TODO hardcoded
                    s3_key=s3_key,
                    content_type=self._get_content_type(ext),
                    ext=ext,
                    file_size=file_size,
                    is_thumbnail=is_thumbnail,
                )

                file_ids.append(file_id)
                thumb_marker = " (thumbnail)" if is_thumbnail else ""
                self._log(
                    f"  File record saved: {original_filename} -> {stored_name}{thumb_marker}"
                )

            except Exception as e:
                self._log(f"  Failed to save file record for {original_filename}: {e}", "warning")

        if file_ids:
            self._log(f"Saved {len(file_ids)} file record(s)")

        return file_ids

    def _get_content_type(self, ext: str) -> str:
        """Get MIME type from file extension."""
        content_types = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
            "svg": "image/svg+xml",
        }
        return content_types.get(ext.lower(), "application/octet-stream")

    async def _resolve_category_hierarchy_with_session(
        self, session, categories: List[str], user_id: int = 1
    ) -> tuple[List[int], Optional[int], List[CategoryInfo]]:
        """
        Resolve category hierarchy from folder path to database IDs.
        Uses an existing database session (no commit - caller handles it).

        Args:
            session: Existing database session
            categories: List of category names from folder path
            user_id: Owner user ID for new categories

        Returns:
            Tuple of (category_ids, deepest_category_id, category_infos)
        """
        if not categories:
            return [], None, []

        from db import CategoryRepository

        category_ids = []
        category_infos = []

        cat_repo = CategoryRepository(session)

        # Use insert_category_hierarchy for atomic upsert
        deepest_id = await cat_repo.insert_category_hierarchy(categories, user_id)

        if deepest_id:
            # Get the full hierarchy to build category_ids and category_infos
            parent_id = None
            group_id = None

            for level, cat_name in enumerate(categories):
                cat = await cat_repo.get_by_title(cat_name, user_id)
                if cat:
                    cat_id = cat.id
                    if level == 0:
                        group_id = cat_id

                    category_ids.append(cat_id)

                    cat_info = CategoryInfo(
                        id=cat_id,
                        title=cat_name,
                        level=level,
                        parent_id=parent_id,
                        group_id=group_id,
                        user_id=user_id,
                    )
                    category_infos.append(cat_info)
                    parent_id = cat_id

                    self._log(f"  Category '{cat_name}' -> ID: {cat_id} (level: {level})")

        deepest_id = category_ids[-1] if category_ids else None
        return category_ids, deepest_id, category_infos

    async def _resolve_category_hierarchy(
        self, categories: List[str], user_id: int = 1
    ) -> tuple[List[int], Optional[int], List[CategoryInfo]]:
        """
        Resolve category hierarchy (opens own session - for standalone use).
        Prefer _resolve_category_hierarchy_with_session when you have an existing session.
        """
        if not categories:
            return [], None, []

        from db import get_db_session

        async with get_db_session() as session:
            result = await self._resolve_category_hierarchy_with_session(
                session, categories, user_id
            )
            await session.commit()
            return result

    async def _link_post_to_categories(
        self, session, post_id: int, category_ids: List[int]
    ) -> None:
        """
        Link a post to all categories in its hierarchy.

        Inserts entries into post_categories junction table.

        Args:
            session: Database session
            post_id: The post ID
            category_ids: List of category IDs to link
        """
        if not category_ids:
            return

        from sqlalchemy import func
        from sqlalchemy.dialects.postgresql import insert
        from db.models import PostCategory

        for category_id in category_ids:
            stmt = insert(PostCategory).values(
                post_id=post_id,
                category_id=category_id,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["post_id", "category_id"],
                set_={"updated_at": func.current_timestamp()},
            )
            await session.execute(stmt)

        self._log(f"Linked post {post_id} to {len(category_ids)} categories")

    def _build_upload_payload(
        self,
        data: Dict[str, Any],
        category_ids: List[int] = None,
        category_infos: List[CategoryInfo] = None,
    ) -> UploadPayload:
        """
        Build UploadPayload from processed data.

        Args:
            data: Processed article data including:
                - title, slug, content, tags, categories
                - thumbnail (dict with s3_url, stored_name, etc.)
                - images (list of dicts with s3_url, stored_name, etc.)
                - user_id, username, status, date, etc.
            category_ids: Resolved category snowflake IDs
            category_infos: List of CategoryInfo with full hierarchy info

        Returns:
            UploadPayload ready for processing
        """
        from datetime import datetime

        # Build metadata
        username = data.get("username")
        if not username:
            author = data.get("author", config.DEFAULT_USERNAME)
            username = author.lower().replace(" ", "-") if author else config.DEFAULT_USERNAME

        metadata = ArticleMetadata(
            title=data.get("title", "Untitled"),
            slug=data.get("slug") or generate_slug_from_title(data.get("title", "untitled")),
            user_id=data.get("user_id", config.DEFAULT_USER_ID),
            username=username,
            # Hierarchy fields (for posts: level=0, parent_id=None, group_id set after creation)
            group_id=data.get("group_id"),  # None for new posts, set to self after creation
            level=data.get("level", 0),  # 0 = root post, 1+ = comment/reply
            parent_id=data.get("parent_id"),  # None for posts, parent ID for comments
            priority=data.get("priority", 100),  # Display order
            # Content metadata
            description=data.get("summary") or data.get("description"),
            status=data.get("status", PostStatusEnum.PUBLIC),
            date=str(data.get("date", "")) if data.get("date") else None,
            categories=data.get("categories", []),
            category_ids=category_ids or [],
            category_id=category_ids[-1] if category_ids else None,
            tags=data.get("tags", []),
            word_count=data.get("word_count"),
            reading_time=data.get("reading_time"),
            # SEO fields
            meta_title=data.get("meta_title") or data.get("title", "")[:70],
            meta_description=data.get("meta_description") or (data.get("summary") or data.get("description", ""))[:170],
            og_image_url=data.get("thumbnail", {}).get("s3_url") if isinstance(data.get("thumbnail"), dict) else None,
            og_image_alt=data.get("og_image_alt") or f"{data.get('title', 'Article')} thumbnail",
        )

        # Build thumbnail file info
        thumbnail_data = data.get("thumbnail", {})
        if isinstance(thumbnail_data, dict):
            thumbnail = FileInfo(
                original_filename=thumbnail_data.get(
                    "original_filename", Path(thumbnail_data.get("local_path", "")).name
                ),
                local_path=thumbnail_data.get("local_path"),
                stored_name=thumbnail_data.get("stored_name", ""),
                s3_key=thumbnail_data.get("s3_key", ""),
                s3_url=thumbnail_data.get("s3_url", ""),
                content_type=self._get_content_type(
                    Path(thumbnail_data.get("local_path", "")).suffix.lstrip(".")
                ),
                ext=Path(thumbnail_data.get("local_path", "")).suffix.lstrip("."),
                file_size=(
                    Path(thumbnail_data.get("local_path", "")).stat().st_size
                    if thumbnail_data.get("local_path")
                    and Path(thumbnail_data.get("local_path", "")).exists()
                    else None
                ),
                is_thumbnail=True,
            )
        else:
            # Fallback for string thumbnail path
            thumbnail = FileInfo(
                original_filename=Path(str(thumbnail_data)).name if thumbnail_data else "",
                stored_name="",
                s3_key="",
                s3_url="",
                is_thumbnail=True,
            )

        # Build images list
        images = []
        for img in data.get("images", []):
            if isinstance(img, dict):
                file_info = FileInfo(
                    original_filename=img.get(
                        "original_filename", Path(img.get("local_path", "")).name
                    ),
                    local_path=img.get("local_path"),
                    stored_name=img.get("stored_name", ""),
                    s3_key=img.get("s3_key", ""),
                    s3_url=img.get("s3_url", ""),
                    content_type=self._get_content_type(
                        Path(img.get("local_path", "")).suffix.lstrip(".")
                    ),
                    ext=Path(img.get("local_path", "")).suffix.lstrip("."),
                    file_size=(
                        Path(img.get("local_path", "")).stat().st_size
                        if img.get("local_path") and Path(img.get("local_path", "")).exists()
                        else None
                    ),
                    is_thumbnail=False,
                )
                images.append(file_info)

        # Build published URL
        published_url = f"{config.BLOG_BASE_URL}/blog/@{username}/{metadata.slug}"

        # Build payload
        payload = UploadPayload(
            metadata=metadata,
            content=data.get("content", ""),
            thumbnail=thumbnail,
            images=images,
            category_hierarchy=category_infos or [],
            published_url=published_url,
            source_file=data.get("file_path"),
            processed_at=datetime.now(),
        )

        return payload
