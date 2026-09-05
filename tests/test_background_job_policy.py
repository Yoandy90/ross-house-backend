from rental.background_job_policy import should_disable_background_jobs


def test_staging_is_always_fail_closed():
    assert should_disable_background_jobs(
        {"ENVIRONMENT": "staging", "DISABLE_BACKGROUND_JOBS": "false"}
    )


def test_explicit_kill_switch_disables_jobs():
    assert should_disable_background_jobs({"DISABLE_BACKGROUND_JOBS": "true"})


def test_production_behavior_is_unchanged_without_flag():
    assert not should_disable_background_jobs({"ENVIRONMENT": "production"})


def test_false_flag_does_not_disable_non_staging_environment():
    assert not should_disable_background_jobs(
        {"ENVIRONMENT": "development", "DISABLE_BACKGROUND_JOBS": "false"}
    )
