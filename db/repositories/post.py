# db/repositories/post.py
"""
Post Repository

Handles post operations including creation with Snowflake IDs.
Translates TypeScript insert_post.ts logic to Python/SQLAlchemy.
"""

from typing import Optional, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Post, PostStatusEnum
from db.repositories.base import BaseRepository
from utils.snowflake import generate_id


class PostRepository(BaseRepository[Post]):
    """
    Repository for Post operations.
    
    Supports:
    - CRUD operations (inherited)
    - Post creation with Snowflake ID
    - Hierarchical posts (for comments)
    - Status-based filtering
    
    Usage:
        async with get_db_session() as session:
            repo = PostRepository(session)
            post_id = await repo.create_post(
                user_id=2,
                title="My Post",
                slug="my-post",
                content="...",
                category_id=123,
            )
            await session.commit()
    """
    
    def __init__(self, session: AsyncSession):
        super().__init__(Post, session)
    
    async def get_by_slug(self, slug: str, user_id: Optional[int] = None) -> Optional[Post]:
        """
        Get post by slug.
        
        Args:
            slug: Post slug.
            user_id: Filter by user ID (optional).
        
        Returns:
            Post or None if not found.
        """
        stmt = (
            select(Post)
            .where(Post.slug == slug)
            .where(Post.deleted_at.is_(None))
        )
        if user_id is not None:
            stmt = stmt.where(Post.user_id == user_id)
        
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_user(
        self,
        user_id: int,
        limit: int = 100,
        offset: int = 0,
        status: Optional[PostStatusEnum] = None,
    ) -> List[Post]:
        """
        Get posts by user ID.
        
        Args:
            user_id: User ID.
            limit: Maximum number of posts.
            offset: Number of posts to skip.
            status: Filter by status (optional).
        
        Returns:
            List of posts.
        """
        stmt = (
            select(Post)
            .where(Post.user_id == user_id)
            .where(Post.deleted_at.is_(None))
            .where(Post.level == 0)  # Only root posts, not comments
        )
        
        if status is not None:
            stmt = stmt.where(Post.status == status)
        
        stmt = (
            stmt
            .order_by(Post.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_by_category(
        self,
        category_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Post]:
        """
        Get posts by category ID.
        
        Args:
            category_id: Category ID.
            limit: Maximum number of posts.
            offset: Number of posts to skip.
        
        Returns:
            List of posts.
        """
        stmt = (
            select(Post)
            .where(Post.category_id == category_id)
            .where(Post.deleted_at.is_(None))
            .where(Post.level == 0)
            .order_by(Post.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
    async def create_post(
        self,
        user_id: int,
        title: str,
        slug: str,
        content: Optional[str] = None,
        description: Optional[str] = None,
        category_id: Optional[int] = None,
        status: PostStatusEnum = PostStatusEnum.PUBLIC,
        post_id: Optional[int] = None,
    ) -> int:
        """
        Create a new post with Snowflake ID.
        
        For insert-or-update behavior, use upsert_post() instead.
        """
        if post_id is None:
            post_id = generate_id()
        
        post = Post(
            id=post_id,
            group_id=post_id,
            level=0,
            parent_id=None,
            user_id=user_id,
            title=title,
            slug=slug,
            content=content,
            description=description,
            category_id=category_id,
            status=status,
        )
        
        self.session.add(post)
        await self.session.flush()
        
        return post.id
    
    async def upsert_post(
        self,
        user_id: int,
        title: str,
        slug: str,
        content: Optional[str] = None,
        description: Optional[str] = None,
        category_id: Optional[int] = None,
        status: PostStatusEnum = PostStatusEnum.PUBLIC,
        post_id: Optional[int] = None,
        # SEO fields
        meta_title: Optional[str] = None,
        meta_description: Optional[str] = None,
        og_image_url: Optional[str] = None,
        og_image_alt: Optional[str] = None,
    ) -> int:
        """
        Upsert a post using raw SQL with ON CONFLICT.
        
        Matches the pattern from temp/insert_categories.ts.
        
        Args:
            user_id: Author user ID.
            title: Post title.
            slug: URL-friendly slug.
            content: Post content (markdown).
            description: Post description/summary.
            category_id: Primary category ID.
            status: Post visibility status.
            post_id: Optional pre-generated ID.
            meta_title: SEO title (max 70 chars).
            meta_description: SEO description (max 170 chars).
            og_image_url: OG image URL.
            og_image_alt: OG image alt text.
        
        Returns:
            Upserted post ID.
        """
        from sqlalchemy import text
        
        if post_id is None:
            post_id = generate_id()
        
        query = text("""
            INSERT INTO posts (id, group_id, level, parent_id, user_id, title, slug, content, description, category_id, status, meta_title, meta_description, og_image_url, og_image_alt)
            VALUES (:id, :id, 0, NULL, :user_id, :title, :slug, :content, :description, :category_id, :status, :meta_title, :meta_description, :og_image_url, :og_image_alt)
            ON CONFLICT (user_id, slug) DO UPDATE SET
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                description = EXCLUDED.description,
                category_id = EXCLUDED.category_id,
                status = EXCLUDED.status,
                meta_title = EXCLUDED.meta_title,
                meta_description = EXCLUDED.meta_description,
                og_image_url = EXCLUDED.og_image_url,
                og_image_alt = EXCLUDED.og_image_alt,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
        """)
        
        result = await self.session.execute(query, {
            "id": post_id,
            "user_id": user_id,
            "title": title,
            "slug": slug,
            "content": content,
            "description": description,
            "category_id": category_id,
            "status": status.value if hasattr(status, 'value') else status,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "og_image_url": og_image_url,
            "og_image_alt": og_image_alt,
        })
        return result.scalar_one()
    
    async def create_comment(
        self,
        user_id: int,
        parent_id: int,
        title: str,
        content: str,
        slug: Optional[str] = None,
    ) -> int:
        """
        Create a comment on a post.
        
        Args:
            user_id: Commenter user ID.
            parent_id: Parent post/comment ID.
            title: Comment title.
            content: Comment content.
            slug: Optional slug.
        
        Returns:
            Created comment ID.
        """
        # Get parent to determine hierarchy
        parent = await self.get_by_id(parent_id)
        if parent is None:
            raise ValueError(f"Parent post {parent_id} not found")
        
        comment_id = generate_id()
        
        comment = Post(
            id=comment_id,
            group_id=parent.group_id,  # Same group as root post
            level=parent.level + 1,
            parent_id=parent_id,
            user_id=user_id,
            title=title,
            slug=slug or f"comment-{comment_id}",
            content=content,
            status=PostStatusEnum.PUBLIC,
        )
        
        self.session.add(comment)
        await self.session.flush()
        
        return comment.id
    
    async def get_comments(self, post_id: int) -> List[Post]:
        """
        Get all comments for a post.
        
        Args:
            post_id: Root post ID.
        
        Returns:
            List of comments ordered by created_at.
        """
        stmt = (
            select(Post)
            .where(Post.group_id == post_id)
            .where(Post.id != post_id)  # Exclude root post
            .where(Post.deleted_at.is_(None))
            .order_by(Post.created_at)
        )
        
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
    async def update_category(self, post_id: int, category_id: int) -> bool:
        """
        Update post's primary category.
        
        Args:
            post_id: Post ID.
            category_id: New category ID.
        
        Returns:
            True if updated, False if post not found.
        """
        result = await self.update(post_id, {"category_id": category_id})
        return result is not None
    
    async def count_by_user(self, user_id: int) -> int:
        """
        Count posts by user.
        
        Args:
            user_id: User ID.
        
        Returns:
            Number of posts.
        """
        stmt = (
            select(func.count())
            .select_from(Post)
            .where(Post.user_id == user_id)
            .where(Post.level == 0)
            .where(Post.deleted_at.is_(None))
        )
        
        result = await self.session.execute(stmt)
        return result.scalar() or 0
