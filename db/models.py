# db/models.py
"""
SQLAlchemy ORM Models

Models matching the PostgreSQL schema in schema/posts.schema.sql.
All models inherit from Base and use common mixins for timestamps and soft delete.
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin, SoftDeleteMixin


# ===========================
# ENUMS
# ===========================

class PostStatusEnum(str, PyEnum):
    """Post visibility status."""
    PUBLIC = "public"
    PRIVATE = "private"
    FOLLOWER = "follower"


class UsersAuthProvider(str, PyEnum):
    """User authentication provider."""
    APP = "app"
    FACEBOOK = "facebook"
    GOOGLE = "google"
    GITHUB = "github"
    KAKAO = "kakao"
    NAVER = "naver"
    APPLE = "apple"
    TWITTER = "twitter"
    LINKEDIN = "linkedin"
    MICROSOFT = "microsoft"
    YAHOO = "yahoo"


# ===========================
# MODELS
# ===========================

class User(Base, TimestampMixin, SoftDeleteMixin):
    """
    User model matching users table.
    
    Supports multiple auth providers (OAuth, app-based).
    """
    __tablename__ = "users"
    
    seq: Mapped[int] = mapped_column(
        BigInteger,
        autoincrement=True,
        unique=True,
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    username: Mapped[Optional[str]] = mapped_column(
        String(50),
        unique=True,
        nullable=True,
    )
    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )
    password_hash: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    provider: Mapped[UsersAuthProvider] = mapped_column(
        Enum(UsersAuthProvider, name="users_auth_provider", create_type=False),
        default=UsersAuthProvider.APP,
        nullable=False,
    )
    provider_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )
    
    # Relationships
    posts: Mapped[List["Post"]] = relationship(
        "Post",
        back_populates="user",
        lazy="selectin",
    )
    categories: Mapped[List["Category"]] = relationship(
        "Category",
        back_populates="user",
        lazy="selectin",
        primaryjoin="User.id == Category.user_id",
        foreign_keys="Category.user_id",
    )
    files: Mapped[List["File"]] = relationship(
        "File",
        back_populates="user",
        lazy="selectin",
    )


class Post(Base, TimestampMixin, SoftDeleteMixin):
    """
    Post model matching posts table.
    
    Supports hierarchical structure:
    - Post (root): group_id = self, parent_id = NULL, level = 0
    - Comment: group_id = root post, parent_id = post/comment, level = 1+
    """
    __tablename__ = "posts"
    
    seq: Mapped[int] = mapped_column(
        BigInteger,
        autoincrement=True,
        unique=True,
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    group_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )
    level: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    parent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=True,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        default=100,
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    category_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    # SEO fields
    meta_title: Mapped[Optional[str]] = mapped_column(
        String(70),
        nullable=True,
    )
    meta_description: Mapped[Optional[str]] = mapped_column(
        String(170),
        nullable=True,
    )
    og_image_url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    og_image_alt: Mapped[Optional[str]] = mapped_column(
        String(125),
        nullable=True,
    )
    status: Mapped[PostStatusEnum] = mapped_column(
        Enum(PostStatusEnum, name="post_status_enum", create_type=False),
        default=PostStatusEnum.PUBLIC,
        nullable=False,
    )
    
    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="posts",
    )
    parent: Mapped[Optional["Post"]] = relationship(
        "Post",
        remote_side=[id],
        backref="children",
    )
    files: Mapped[List["File"]] = relationship(
        "File",
        back_populates="post",
        lazy="selectin",
    )
    post_tags: Mapped[List["PostTag"]] = relationship(
        "PostTag",
        back_populates="post",
        lazy="selectin",
    )
    post_categories: Mapped[List["PostCategory"]] = relationship(
        "PostCategory",
        back_populates="post",
        lazy="selectin",
    )
    
    # Indexes (defined at table level)
    __table_args__ = (
        Index("idx_posts_user_id", "user_id"),
        Index("idx_posts_slug_created", "slug", "created_at", "id"),
    )


class Category(Base, TimestampMixin, SoftDeleteMixin):
    """
    Category model matching categories table.
    
    Hierarchical structure:
    - Root: group_id = self, parent_id = NULL, level = 0
    - Child: group_id = root, parent_id = parent, level = parent.level + 1
    """
    __tablename__ = "categories"
    
    seq: Mapped[int] = mapped_column(
        BigInteger,
        autoincrement=True,
        unique=True,
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    group_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )
    level: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        default=100,
        nullable=False,
    )
    parent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(100),
        nullable=False,  # unique per user, not globally
    )
    
    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="categories",
        foreign_keys=[user_id],
        primaryjoin="Category.user_id == User.id",
    )
    parent: Mapped[Optional["Category"]] = relationship(
        "Category",
        remote_side=[id],
        backref="children",
    )
    
    __table_args__ = (
        UniqueConstraint("user_id", "title", name="categories_user_title_key"),
        Index("idx_categories_parent_id", "parent_id"),
        Index("idx_categories_level", "level"),
        Index("idx_categories_user_title", "user_id", "title"),
    )


class File(Base, TimestampMixin, SoftDeleteMixin):
    """
    File model matching files table.
    
    Stores file metadata for posts (thumbnails, images).
    Actual files are stored in S3.
    """
    __tablename__ = "files"
    
    seq: Mapped[int] = mapped_column(
        BigInteger,
        autoincrement=True,
        unique=True,
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=True,
    )
    post_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("posts.id"),
        nullable=True,
    )
    content_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    ext: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
    )
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    stored_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    s3_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    stored_uri: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    file_size: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    is_thumbnail: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    
    # Relationships
    user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="files",
    )
    post: Mapped[Optional["Post"]] = relationship(
        "Post",
        back_populates="files",
    )
    
    __table_args__ = (
        Index("idx_files_user_id", "user_id"),
        Index("idx_files_post_id", "post_id"),
    )


class Tag(Base, TimestampMixin, SoftDeleteMixin):
    """Tag model matching tags table."""
    __tablename__ = "tags"
    
    seq: Mapped[int] = mapped_column(
        BigInteger,
        autoincrement=True,
        unique=True,
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    title: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
    )
    
    __table_args__ = (
        Index("idx_tags_title", "title"),
    )


class PostTag(Base, TimestampMixin, SoftDeleteMixin):
    """
    Post-Tag junction table.
    
    Links posts to tags (many-to-many).
    """
    __tablename__ = "post_tags"
    
    seq: Mapped[int] = mapped_column(
        BigInteger,
        autoincrement=True,
        unique=True,
    )
    post_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("posts.id"),
        primary_key=True,
        nullable=False,
    )
    tag_title: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
        nullable=False,
    )
    
    # Relationships
    post: Mapped["Post"] = relationship(
        "Post",
        back_populates="post_tags",
    )
    
    __table_args__ = (
        Index("idx_post_tags_tag_title", "tag_title"),
        Index("idx_post_tags_post_id", "post_id"),
    )


class PostCategory(Base, TimestampMixin, SoftDeleteMixin):
    """
    Post-Category junction table.
    
    Links posts to categories (many-to-many).
    """
    __tablename__ = "post_categories"
    
    seq: Mapped[int] = mapped_column(
        BigInteger,
        autoincrement=True,
        unique=True,
    )
    post_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("posts.id"),
        primary_key=True,
        nullable=False,
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("categories.id"),
        primary_key=True,
        nullable=False,
    )
    
    # Relationships
    post: Mapped["Post"] = relationship(
        "Post",
        back_populates="post_categories",
    )
    category: Mapped["Category"] = relationship("Category")
    
    __table_args__ = (
        Index("idx_post_categories_category_id", "category_id"),
        Index("idx_post_categories_post_id", "post_id"),
    )
