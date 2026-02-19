"""Shared Supabase client for all API routes."""
import os
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET = os.environ["SUPABASE_SECRET"]  # secret key for server-side writes
SUPABASE_PUBLISHABLE = os.environ["SUPABASE_PUBLISHABLE"]  # publishable key for reads

def get_db() -> Client:
    """Get Supabase client with secret key (server-side, full access)."""
    return create_client(SUPABASE_URL, SUPABASE_SECRET)

def get_public_db() -> Client:
    """Get Supabase client with publishable key (read-only)."""
    return create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE)
