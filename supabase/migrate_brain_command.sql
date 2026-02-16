-- Migration: Add 'update_pair_config' to bot_commands CHECK constraint
-- Run this against the live Supabase database.

-- Drop the old constraint and re-create with the new command included.
ALTER TABLE public.bot_commands DROP CONSTRAINT IF EXISTS bot_commands_command_check;
ALTER TABLE public.bot_commands
    ADD CONSTRAINT bot_commands_command_check
    CHECK (command IN ('pause', 'resume', 'force_strategy', 'update_config', 'update_pair_config'));
