"""Schemas for backend/app/api/auth.py — Integral signup + password reset bodies."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr


class ExtendedUserCreate(BaseModel):
    """Extended user creation model with additional fields."""

    email: EmailStr
    password: str
    name: str
    workspaceName: Optional[str] = None


class ForgotPasswordRequest(BaseModel):
    """Request body for ``POST /auth/forgot-password``."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Request body for ``POST /auth/reset-password``."""

    token: str
    password: str


class VerifyEmailRequest(BaseModel):
    """Request body for ``POST /auth/verify-email``."""

    code: str
