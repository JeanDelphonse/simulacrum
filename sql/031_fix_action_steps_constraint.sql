-- Drop the unique constraint (agent_action_id, step_number) which incorrectly
-- prevents per-contact agents from creating more than one contact per step number.
-- Idempotency is now enforced in code (create_steps_from_artifact guard).
ALTER TABLE action_steps
  DROP INDEX IF EXISTS uq_step_action_num;
