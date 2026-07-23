def test_topcv_pipeline_imports_and_is_a_flow():
    from orchestration.flows.topcv_pipeline import topcv_pipeline
    # Prefect flows expose a .name attribute
    assert topcv_pipeline.name == "topcv-pipeline"
