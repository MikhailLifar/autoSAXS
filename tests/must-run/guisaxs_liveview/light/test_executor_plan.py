"""Commit-gate liveview light: executor / plan unit tests (no full GUI E2E)."""
from __future__ import annotations

from pathlib import Path

from guisaxs_skills.liveview.pipeline import Job, JobStep, LiveviewJobExecutor
from guisaxs_skills.liveview.session.sample_store import SampleStore
from guisaxs_skills.core.models import RunRequest
from guisaxs_skills.liveview.session import LiveviewSessionState

def test_executor_requeues_cancelled_job_before_normal_jobs(tmp_path: Path):
    """
    Unit-ish check: when cancel_current() causes a step to fail, the executor requeues the current job
    with priority between rerun (100) and normal (0), i.e. it should be chosen before normal jobs.
    """

    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

        def start(self, _req) -> None:
            return None

    runner = _DummyRunner()
    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=runner)  # type: ignore[arg-type]

    current = Job(
        id="cur",
        priority=0,
        steps=[JobStep(name="integrate", request=RunRequest("integrate", [], {}))],
        context={"tiff_path": str(tmp_path / "a.tif")},
    )
    current = current.mark_step_done("integrate", result={"integrated_1d": "x.dat"})
    normal = Job(
        id="norm",
        priority=0,
        steps=[JobStep(name="integrate", request=RunRequest("integrate", [], {}))],
        context={},
    )
    rerun = Job(
        id="rerun",
        priority=100,
        steps=[JobStep(name="subtract", request=RunRequest("subtract", [], {}))],
        context={},
    )

    ex._current_job = current  # noqa: SLF001
    ex._pending_step_name = "integrate"  # noqa: SLF001
    ex._jobs.put(normal)  # noqa: SLF001
    ex._jobs.put(rerun)  # noqa: SLF001

    ex.cancel_current()
    from guisaxs_skills.logic.runner_qprocess import RunOutcome

    ex._on_skill_finished(RunOutcome(success=False, exit_code=15, result={}, request=None))  # noqa: SLF001

    j1 = ex._jobs.get_nowait()  # noqa: SLF001
    assert j1 is not None and j1.id == "rerun"
    j2 = ex._jobs.get_nowait()  # noqa: SLF001
    assert j2 is not None and j2.id.startswith("cur:retry:")
    assert j2.completed.results.get("integrate") == {"integrated_1d": "x.dat"}
    j3 = ex._jobs.get_nowait()  # noqa: SLF001
    assert j3 is not None and j3.id == "norm"


def test_executor_paused_starts_manual_jobs_only(tmp_path: Path):
    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()
            self.started: list[RunRequest] = []

        def is_running(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

        def start(self, req) -> None:
            self.started.append(req)

    runner = _DummyRunner()
    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=runner)  # type: ignore[arg-type]

    auto = Job(
        id="auto",
        priority=0,
        steps=[JobStep(name="integrate", request=RunRequest("integrate", [], {}))],
        context={"tiff_path": str(tmp_path / "a.tif")},
    )
    manual = Job(
        id="manual",
        priority=150,
        steps=[JobStep(name="fit_guinier", request=RunRequest("fit_guinier", ["prof.dat"], {}))],
        context={"manual": True, "monodisperse": True},
    )
    ex._jobs.put(auto)  # noqa: SLF001
    ex._jobs.put(manual)  # noqa: SLF001
    state.set_auto_processing(False)

    ex._tick()  # noqa: SLF001
    ex._tick()  # noqa: SLF001

    assert ex._current_job is not None and ex._current_job.id == "manual"  # noqa: SLF001
    assert runner.started and runner.started[0].skill_name == "fit_guinier"
    assert len(ex._jobs) == 1  # noqa: SLF001
    assert ex._jobs.get_nowait().id == "auto"  # noqa: SLF001


def test_executor_paused_advances_manual_multi_step_job(tmp_path: Path):
    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()
            self.started: list[str] = []

        def is_running(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

        def start(self, req) -> None:
            self.started.append(req.skill_name)

    runner = _DummyRunner()
    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=runner)  # type: ignore[arg-type]
    state.set_auto_processing(False)

    manual = Job(
        id="chain",
        priority=150,
        steps=[
            JobStep(name="fit_guinier", request=RunRequest("fit_guinier", ["prof.dat"], {})),
            JobStep(name="fit_distances", request=RunRequest("fit_distances", ["prof.dat"], {})),
        ],
        context={"manual": True, "monodisperse": True},
    )
    ex._start_job(manual)  # noqa: SLF001
    ex._tick()  # noqa: SLF001
    assert runner.started == ["fit_guinier"]

    from guisaxs_skills.logic.runner_qprocess import RunOutcome

    ex._on_skill_finished(  # noqa: SLF001
        RunOutcome(success=True, exit_code=0, result={"rg": 1.0}, request=None)
    )
    ex._tick()  # noqa: SLF001
    assert runner.started == ["fit_guinier", "fit_distances"]


def test_monodisperse_guinier_opts_fixed_interval_from_spinboxes(tmp_path: Path):
    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "sub_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    state = LiveviewSessionState(watchdir=tmp_path)
    state.monodisperse_wizard_params = {"first": 1, "last": 1}
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]

    steps = ex.monodisperse_steps_guinier_and_distances(
        str(prof),
        output_root=tmp_path,
        fixed_guinier_interval=True,
        guinier_interval_first=8,
        guinier_interval_last=32,
    )
    assert [s.name for s in steps] == [
        "fit_guinier",
        "analyze_kratky",
        "fit_distances",
        "confirm_shape",
    ]
    g_opts = steps[0].request.options
    k_opts = steps[1].request.options
    d_opts = steps[2].request.options
    assert g_opts["first"] == 8
    assert g_opts["last"] == 32
    assert k_opts["rg_nm"] == "${fit_guinier.rg}"
    assert k_opts["i0"] == "${fit_guinier.i0}"
    assert d_opts["rg_nm"] == "${fit_guinier.rg}"
    assert d_opts["first"] == "${fit_guinier.first_point_1based}"
    # Guinier last must not be forwarded to DATGNOM (window too narrow for p(r)).
    assert "last" not in d_opts


def test_analysis_steps_both_armed_separate_guinier(tmp_path: Path):
    from guisaxs_skills.liveview.pipeline.executor import LiveviewJobExecutor
    from guisaxs_skills.liveview.pipeline.monodisperse_pipeline import (
        FIT_GUINIER_MONO_STEP,
        FIT_GUINIER_POLY_STEP,
    )

    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "int_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    state = LiveviewSessionState(watchdir=tmp_path)
    state.monodisperse_armed = True
    state.polydisperse_armed = True
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]
    steps = ex._analysis_steps_for_profile(str(prof), output_root=tmp_path)  # noqa: SLF001
    names = [s.name for s in steps]
    assert FIT_GUINIER_MONO_STEP in names
    assert FIT_GUINIER_POLY_STEP in names
    assert "fit_distances" in names
    assert "fit_sizes" in names
    assert "confirm_shape" in names
    assert "confirm_dr" in names
    g_mono = next(s for s in steps if s.name == FIT_GUINIER_MONO_STEP)
    g_poly = next(s for s in steps if s.name == FIT_GUINIER_POLY_STEP)
    assert "guinier_mono" in str(g_mono.request.options.get("output_dir", "")).replace("\\", "/")
    assert "guinier_poly" in str(g_poly.request.options.get("output_dir", "")).replace("\\", "/")


def test_polydisperse_steps_full_defaults(tmp_path: Path):
    from guisaxs_skills.liveview.pipeline.polydisperse_pipeline import (
        PolydispersePipelineParts,
        build_polydisperse_steps,
    )
    from guisaxs_skills.liveview.pipeline.monodisperse_pipeline import FIT_GUINIER_POLY_STEP
    from guisaxs_skills.liveview.session.state import PolydisperseMixtureMode

    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "sub_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]

    steps = build_polydisperse_steps(
        str(prof),
        output_root=tmp_path,
        state=state,
        parts=PolydispersePipelineParts.FULL,
        load_yaml=ex._load_yaml_options,  # noqa: SLF001
    )
    assert [s.name for s in steps] == [FIT_GUINIER_POLY_STEP, "fit_sizes", "confirm_dr"]
    s_opts = steps[1].request.options
    assert s_opts["shape"] == "spheres"
    assert s_opts["first"] == 1

    # MIXTURE skill stays Confirm-only in guisaxs-dr; pipeline only adds confirm_dr.
    state.polydisperse_mixture_mode = PolydisperseMixtureMode.MIXTURE
    steps2 = build_polydisperse_steps(
        str(prof),
        output_root=tmp_path,
        state=state,
        parts=PolydispersePipelineParts.FULL,
        load_yaml=ex._load_yaml_options,  # noqa: SLF001
    )
    assert [s.name for s in steps2] == [FIT_GUINIER_POLY_STEP, "fit_sizes", "confirm_dr"]


def test_polydisperse_mixture_opts_include_explicit_bounds(tmp_path: Path):
    from guisaxs_skills.liveview.pipeline.polydisperse_pipeline import model_mixture_opts

    state = LiveviewSessionState(watchdir=tmp_path)
    state.polydisperse_window_params = {
        "mixture": {"max_nph": 2, "r_max": 9.5, "poly_max": 3.0},
    }
    opts = model_mixture_opts(state=state, output_root=tmp_path)
    assert opts["max_nph"] == 2
    assert opts["r_max"] == 9.5
    assert opts["poly_max"] == 3.0


def test_polydisperse_guinier_only_no_sizes_first_handoff(tmp_path: Path):
    from guisaxs_skills.liveview.pipeline.polydisperse_pipeline import (
        PolydispersePipelineParts,
        build_polydisperse_steps,
        fit_sizes_opts,
    )

    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "sub_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    state = LiveviewSessionState(watchdir=tmp_path)
    state.polydisperse_window_params = {"guinier_first": 5, "guinier_last": 20, "first": 1}
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]

    steps = build_polydisperse_steps(
        str(prof),
        output_root=tmp_path,
        state=state,
        parts=PolydispersePipelineParts.GUINIER_ONLY,
        load_yaml=ex._load_yaml_options,  # noqa: SLF001
        fixed_guinier_interval=True,
        guinier_interval_first=5,
        guinier_interval_last=20,
    )
    assert [s.name for s in steps] == ["fit_guinier_poly"]
    assert steps[0].request.options["first"] == 5
    assert steps[0].request.options["last"] == 20

    s_opts = fit_sizes_opts(state=state, output_root=tmp_path, load_yaml=ex._load_yaml_options)  # noqa: SLF001
    assert s_opts["first"] == 1
    assert s_opts["shape"] == "spheres"


def test_monodisperse_step_shape_dammif_not_queued(tmp_path: Path):
    """DAMMIF shape is Confirm-only in guisaxs-shape — no JobStep on the liveview queue."""

    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "sub_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    fd = tmp_path / "fit_distances" / "sample"
    fd.mkdir(parents=True)
    gnom = fd / "gnom_best.out"
    gnom.write_text("GNOM mock\n", encoding="utf-8")

    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]

    step = ex.monodisperse_step_shape(
        str(prof),
        output_root=tmp_path,
        shape_mode="dammif",
        gnom_out_path=str(gnom),
    )
    assert step is None


