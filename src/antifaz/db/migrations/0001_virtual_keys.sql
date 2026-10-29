-- Migration 0001: the virtual keys table (issue 121, ADR-0020, ADR-0021).
--
-- Applied by `antifaz db migrate` in one transaction, together with its row in
-- antifaz_schema_version (version 1 and the SHA-256 of this file). Never edit this file once
-- released: a changed checksum is refused (migration_changed). Changes go in a new file.
--
-- The CHECKs repeat the rules of antifaz/keys/records.py as closely as SQL allows. The full
-- Unicode rules of a key name (every Default_Ignorable_Code_Point, the categories Cf, Cs, Co
-- and Cn) stay in Python: here only the controls, the line and paragraph separators, the
-- common invisible characters and whitespace at either end are refused.

CREATE FUNCTION antifaz_scopes_are_distinct(scopes text[]) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    SET search_path = pg_catalog, pg_temp
    RETURN (SELECT count(DISTINCT scope) = cardinality(scopes) FROM unnest(scopes) AS scope);

CREATE TABLE virtual_keys (
    -- The key's fixed number (key=N in the logs). Never given by hand, never reused.
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL
        CONSTRAINT virtual_keys_name_length CHECK (char_length(name) BETWEEN 1 AND 64)
        -- C0 and C1 controls, DEL, U+2028 and U+2029, the zero-width and direction marks
        -- (U+200B-U+200F, U+202A-U+202E, U+2060-U+206F), U+00AD, U+034F, U+061C, U+FEFF and
        -- U+2800 (BRAILLE PATTERN BLANK).
        CONSTRAINT virtual_keys_name_visible CHECK (
            name !~ '[\u0001-\u001f\u007f-\u009f\u00ad\u034f\u061c\u200b-\u200f\u2028-\u202e\u2060-\u206f\u2800\ufeff]'
        )
        -- No whitespace at either end: every character Python's str.strip() removes that is
        -- not already a control (those are refused anywhere above).
        CONSTRAINT virtual_keys_name_trimmed CHECK (
            name !~ '^[ \u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]'
            AND name !~ '[ \u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]$'
        ),
    scopes text[] NOT NULL
        CONSTRAINT virtual_keys_scopes_count CHECK (
            array_ndims(scopes) = 1 AND cardinality(scopes) BETWEEN 1 AND 4
        )
        CONSTRAINT virtual_keys_scopes_known CHECK (
            scopes <@ ARRAY['openai_chat', 'anthropic_messages', 'models', 'scan']::text[]
            AND array_position(scopes, NULL) IS NULL
        )
        CONSTRAINT virtual_keys_scopes_distinct CHECK (antifaz_scopes_are_distinct(scopes)),
    created_at timestamptz NOT NULL
        CONSTRAINT virtual_keys_created_finite CHECK (isfinite(created_at)),
    expires_at timestamptz NULL
        CONSTRAINT virtual_keys_expires_after_creation CHECK (
            expires_at IS NULL OR (isfinite(expires_at) AND expires_at > created_at)
        ),
    revoked_at timestamptz NULL
        CONSTRAINT virtual_keys_revoked_after_creation CHECK (
            revoked_at IS NULL OR (isfinite(revoked_at) AND revoked_at >= created_at)
        ),
    -- HMAC-SHA256 of the key with the pepper of pepper_version. Never the key itself.
    key_hash bytea NOT NULL
        CONSTRAINT virtual_keys_hash_length CHECK (octet_length(key_hash) = 32),
    pepper_version smallint NOT NULL
        CONSTRAINT virtual_keys_pepper_version_positive CHECK (pepper_version >= 1),
    per_minute_limit integer NULL
        CONSTRAINT virtual_keys_per_minute_positive CHECK (
            per_minute_limit IS NULL OR per_minute_limit >= 1
        ),
    per_day_limit integer NULL
        CONSTRAINT virtual_keys_per_day_positive CHECK (per_day_limit IS NULL OR per_day_limit >= 1),
    CONSTRAINT virtual_keys_hash_unique UNIQUE (pepper_version, key_hash)
);

-- Keys are never deleted, only revoked: their number stays for the evidence log (item 9).
CREATE FUNCTION antifaz_virtual_keys_refuse_delete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'virtual keys are never deleted, only revoked'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

-- What identifies a key never changes, and a revocation is final.
CREATE FUNCTION antifaz_virtual_keys_refuse_frozen_update() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.key_hash IS DISTINCT FROM OLD.key_hash
        OR NEW.pepper_version IS DISTINCT FROM OLD.pepper_version
        OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'the number, hash, pepper version and creation of a key never change'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
        RAISE EXCEPTION 'a revoked key stays revoked, with its first revocation time'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER virtual_keys_refuse_delete
    BEFORE DELETE ON virtual_keys
    FOR EACH ROW EXECUTE FUNCTION antifaz_virtual_keys_refuse_delete();

CREATE TRIGGER virtual_keys_refuse_truncate
    BEFORE TRUNCATE ON virtual_keys
    FOR EACH STATEMENT EXECUTE FUNCTION antifaz_virtual_keys_refuse_delete();

CREATE TRIGGER virtual_keys_refuse_frozen_update
    BEFORE UPDATE ON virtual_keys
    FOR EACH ROW EXECUTE FUNCTION antifaz_virtual_keys_refuse_frozen_update();
