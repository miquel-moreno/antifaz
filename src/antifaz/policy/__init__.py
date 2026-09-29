"""Policy: decides what to do with each type of data.

Actions: mask, surrogate, block, allow or route_local. Policies are versioned YAML and
the policy hash goes into every evidence record.

Forbidden: Never decide without a policy version. Never default to allow on detector errors.
"""
