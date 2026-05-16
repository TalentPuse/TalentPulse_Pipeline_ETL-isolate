"""Production normalization engine for TalentPulse pipeline.

Reads rules from normalization.* DB tables (updateable at runtime),
normalizes raw.job_detail, writes results to normalization.job_normalization,
and logs unmapped values to normalization.drift_log.
"""
