# db/repositories/file.py
"""
File Repository

Handles file metadata operations (S3 file references).
Translates TypeScript insert_image.ts logic to Python/SQLAlchemy.
"""

from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import File
from db.repositories.base import BaseRepository
from utils.snowflake import generate_id


class FileRepository(BaseRepository[File]):
    """
    Repository for File operations.
    
    Supports:
    - CRUD operations (inherited)
    - File metadata creation with S3 references
    - Thumbnail management
    - Post-associated file queries
    
    Usage:
        async with get_db_session() as session:
            repo = FileRepository(session)
            file_id = await repo.create_file_record(
                user_id=2,
                post_id=123,
                original_filename="image.png",
                stored_name="article-slug.png",
                s3_key="2/tech/ai/slug/slug.png",
                stored_uri="https://cdn.example.com/...",
                is_thumbnail=True,
            )
            await session.commit()
    """
    
    def __init__(self, session: AsyncSession):
        super().__init__(File, session)
    
    async def get_by_post(self, post_id: int) -> List[File]:
        """
        Get all files for a post.
        
        Args:
            post_id: Post ID.
        
        Returns:
            List of files.
        """
        stmt = (
            select(File)
            .where(File.post_id == post_id)
            .where(File.deleted_at.is_(None))
            .order_by(File.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_thumbnail(self, post_id: int) -> Optional[File]:
        """
        Get thumbnail file for a post.
        
        Args:
            post_id: Post ID.
        
        Returns:
            Thumbnail file or None.
        """
        stmt = (
            select(File)
            .where(File.post_id == post_id)
            .where(File.is_thumbnail == True)
            .where(File.deleted_at.is_(None))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_s3_key(self, s3_key: str) -> Optional[File]:
        """
        Get file by S3 key.
        
        Args:
            s3_key: S3 key path.
        
        Returns:
            File or None.
        """
        stmt = (
            select(File)
            .where(File.s3_key == s3_key)
            .where(File.deleted_at.is_(None))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def create_file_record(
        self,
        user_id: int,
        post_id: int,
        original_filename: str,
        stored_name: str,
        stored_uri: str,
        s3_key: Optional[str] = None,
        content_type: Optional[str] = None,
        ext: Optional[str] = None,
        file_size: Optional[int] = None,
        is_thumbnail: bool = False,
        file_id: Optional[int] = None,
    ) -> int:
        """
        Create file metadata record (after S3 upload).
        
        For insert-or-update behavior, use upsert_file_record() instead.
        """
        if file_id is None:
            file_id = generate_id()
        
        file = File(
            id=file_id,
            user_id=user_id,
            post_id=post_id,
            original_filename=original_filename,
            stored_name=stored_name,
            s3_key=s3_key,
            stored_uri=stored_uri,
            content_type=content_type,
            ext=ext,
            file_size=file_size,
            is_thumbnail=is_thumbnail,
        )
        
        self.session.add(file)
        await self.session.flush()
        
        return file.id
    
    async def upsert_file_record(
        self,
        user_id: int,
        post_id: int,
        original_filename: str,
        stored_name: str,
        stored_uri: str,
        s3_key: Optional[str] = None,
        content_type: Optional[str] = None,
        ext: Optional[str] = None,
        file_size: Optional[int] = None,
        is_thumbnail: bool = False,
        file_id: Optional[int] = None,
    ) -> int:
        """
        Upsert file metadata record using raw SQL with ON CONFLICT.
        
        Matches the pattern from temp/insert_categories.ts.
        
        Args:
            user_id: Owner user ID.
            post_id: Associated post ID.
            original_filename: Original filename from user.
            stored_name: Slug-based storage name.
            stored_uri: Full CDN URL.
            s3_key: S3 key path.
            content_type: MIME type.
            ext: File extension.
            file_size: Size in bytes.
            is_thumbnail: Whether this is the post thumbnail.
            file_id: Optional pre-generated ID.
        
        Returns:
            Upserted file record ID.
        """
        from sqlalchemy import text
        
        if file_id is None:
            file_id = generate_id()
        
        query = text("""
            INSERT INTO files (id, user_id, post_id, original_filename, stored_name, stored_uri, s3_key, content_type, ext, file_size, is_thumbnail)
            VALUES (:id, :user_id, :post_id, :original_filename, :stored_name, :stored_uri, :s3_key, :content_type, :ext, :file_size, :is_thumbnail)
            ON CONFLICT (user_id, stored_name) DO UPDATE SET
                post_id = EXCLUDED.post_id,
                original_filename = EXCLUDED.original_filename,
                stored_uri = EXCLUDED.stored_uri,
                s3_key = EXCLUDED.s3_key,
                content_type = EXCLUDED.content_type,
                ext = EXCLUDED.ext,
                file_size = EXCLUDED.file_size,
                is_thumbnail = EXCLUDED.is_thumbnail,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
        """)
        
        result = await self.session.execute(query, {
            "id": file_id,
            "user_id": user_id,
            "post_id": post_id,
            "original_filename": original_filename,
            "stored_name": stored_name,
            # TODO client is using s3_key, so I set stored_uri to s3_key
            "stored_uri": s3_key, 
            "s3_key": s3_key,
            "content_type": content_type,
            "ext": ext,
            "file_size": file_size,
            "is_thumbnail": is_thumbnail,
        })
        return result.scalar_one()
    
    async def set_thumbnail(self, post_id: int, file_id: int) -> bool:
        """
        Set a file as the post's thumbnail.
        
        Clears existing thumbnail flag and sets the new one.
        
        Args:
            post_id: Post ID.
            file_id: File ID to set as thumbnail.
        
        Returns:
            True if successful, False if file not found.
        """
        # Clear existing thumbnail
        existing_files = await self.get_by_post(post_id)
        for file in existing_files:
            if file.is_thumbnail:
                await self.update(file.id, {"is_thumbnail": False})
        
        # Set new thumbnail
        result = await self.update(file_id, {"is_thumbnail": True})
        return result is not None
    
    async def get_by_user(
        self,
        user_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> List[File]:
        """
        Get all files by user.
        
        Args:
            user_id: User ID.
            limit: Maximum number of files.
            offset: Number to skip.
        
        Returns:
            List of files.
        """
        stmt = (
            select(File)
            .where(File.user_id == user_id)
            .where(File.deleted_at.is_(None))
            .order_by(File.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
