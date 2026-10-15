import pytest

from image_lab.tibo import MAX_BATCH, JobError, build_job, validate_job


def test_build_job_emits_only_known_keys():
    job = build_job(prompt="x", size_mode="controllable", target_size="512x512", output_dir="out")
    from image_lab.tibo import JOB_KEYS

    assert set(job) <= JOB_KEYS


def test_build_job_requires_exactly_one_prompt_form():
    with pytest.raises(JobError):
        build_job(size_mode="controllable", target_size="512x512", output_dir="out")
    with pytest.raises(JobError):
        build_job(prompt="a", prompts=["b"], size_mode="controllable",
                  target_size="512x512", output_dir="out")


def test_controllable_requires_target_size():
    with pytest.raises(JobError):
        build_job(prompt="x", size_mode="controllable", output_dir="out")


def test_controllable_rejects_malformed_target_size():
    with pytest.raises(JobError):
        build_job(prompt="x", size_mode="controllable", target_size="512", output_dir="out")


def test_invariant_requires_at_least_one_reference():
    with pytest.raises(JobError):
        build_job(prompt="x", size_mode="invariant", output_dir="out")


def test_invariant_job_is_built_with_reference():
    job = build_job(prompt="x", size_mode="invariant",
                    references=["/abs/ref.png"], output_dir="out")
    assert job["size_mode"] == "invariant"
    assert job["references"] == ["/abs/ref.png"]
    assert "target_size" not in job


def test_unknown_size_mode_is_rejected():
    with pytest.raises(JobError):
        build_job(prompt="x", size_mode="magic", output_dir="out")


def test_batch_size_above_limit_is_rejected():
    with pytest.raises(JobError):
        build_job(prompt="x", size_mode="controllable", target_size="512x512",
                  output_dir="out", batch_size=MAX_BATCH + 1)


def test_workers_below_one_is_rejected():
    with pytest.raises(JobError):
        build_job(prompt="x", size_mode="controllable", target_size="512x512",
                  output_dir="out", workers=0)


def test_prompts_list_sets_batch_from_length():
    job = build_job(prompts=["a", "b", "c"], size_mode="invariant",
                    references=["/abs/r.png"], output_dir="out")
    assert job["prompts"] == ["a", "b", "c"]
    assert job["workers"] == 3


def test_validate_job_rejects_unknown_key():
    job = {"prompt": "x", "size_mode": "controllable", "target_size": "512x512",
           "output_dir": "out", "mask": "/abs/mask.png"}
    with pytest.raises(JobError) as exc:
        validate_job(job)
    assert "mask" in str(exc.value)


def test_prompt_may_contain_non_ascii():
    """한국어 프롬프트가 그대로 실려야 한다 (cp949 사고 방지)."""
    job = build_job(prompt="빨간 모자를 파란색으로 — 배경은 그대로",
                    size_mode="controllable", target_size="512x512", output_dir="out")
    assert "빨간 모자" in job["prompt"]


# Critical Issue 1: Empty prompt forms
def test_empty_string_prompt_is_rejected():
    with pytest.raises(JobError) as exc:
        build_job(prompt="", size_mode="controllable", target_size="512x512", output_dir="out")
    assert "prompt" in str(exc.value).lower()


def test_empty_prompts_list_is_rejected():
    with pytest.raises(JobError) as exc:
        build_job(prompts=[], size_mode="controllable", target_size="512x512", output_dir="out")
    assert "prompts" in str(exc.value).lower()


def test_empty_prompts_list_with_explicit_workers_is_rejected():
    with pytest.raises(JobError) as exc:
        build_job(prompts=[], workers=3, size_mode="controllable", target_size="512x512", output_dir="out")
    assert "prompts" in str(exc.value).lower()


def test_empty_items_list_is_rejected():
    with pytest.raises(JobError) as exc:
        build_job(items=[], size_mode="controllable", target_size="512x512", output_dir="out")
    assert "items" in str(exc.value).lower()


# Critical Issue 2: bool type for batch_size/workers
def test_batch_size_as_bool_is_rejected():
    with pytest.raises(JobError) as exc:
        build_job(prompt="x", size_mode="controllable", target_size="512x512",
                  output_dir="out", batch_size=True)
    assert "batch_size" in str(exc.value).lower()


def test_workers_as_bool_is_rejected():
    with pytest.raises(JobError) as exc:
        build_job(prompt="x", size_mode="controllable", target_size="512x512",
                  output_dir="out", workers=True)
    assert "workers" in str(exc.value).lower()


# Additional range checks
def test_batch_size_below_one_is_rejected():
    with pytest.raises(JobError):
        build_job(prompt="x", size_mode="controllable", target_size="512x512",
                  output_dir="out", batch_size=0)


def test_workers_above_limit_is_rejected():
    with pytest.raises(JobError):
        build_job(prompt="x", size_mode="controllable", target_size="512x512",
                  output_dir="out", workers=MAX_BATCH + 1)


# Minor: invariant rejects target_size
def test_invariant_rejects_target_size():
    with pytest.raises(JobError) as exc:
        build_job(prompt="x", size_mode="invariant", references=["/r.png"],
                  output_dir="out", target_size="512x512")
    assert "target_size" in str(exc.value).lower() or "invariant" in str(exc.value).lower()
