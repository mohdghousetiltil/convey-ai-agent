from pathlib import Path

from triconvey_agent.canonical.brain_e import executor
from triconvey_agent.canonical.brain_d.mapper import build_action_plan
from triconvey_agent.canonical.schemas import AnswerObject, FormAction

YAML_DIR = Path(r"C:\Users\moham\projects\triconvey-agent\triconvey-mapping")


def _action(question_id: str, confidence: float = 0.95) -> FormAction:
    return FormAction(
        question_id=question_id,
        field_id="Sec. 32 (1)::Edit::Authority::t282l-1871",
        action="set_text",
        payload="value",
        source_answer_confidence=confidence,
    )


def test_critical_action_detection_prefers_high_risk_questions():
    critical_fid = {"tab": "Sec. 32 (6)", "name": "13. Attachments", "control_type": "Edit"}
    normal_fid = {"tab": "Sec. 32 (3)", "name": "Misc note", "control_type": "Edit"}
    assert executor._is_critical_action(_action("sec32_6_13_attachments"), critical_fid) is True
    assert executor._is_critical_action(_action("sec32_1.1_outgoing_1_amount"), critical_fid) is True
    assert executor._is_critical_action(_action("sec32_misc_note"), normal_fid) is False


def test_write_confidence_threshold_is_higher_for_critical_fields():
    critical_fid = {"tab": "Sec. 32 (6)", "name": "13. Attachments", "control_type": "Edit"}
    normal_fid = {"tab": "Sec. 32 (3)", "name": "Misc note", "control_type": "Edit"}

    assert executor._write_confidence_threshold(_action("sec32_6_13_attachments"), critical_fid) == (
        executor.CRITICAL_CONFIDENCE_REVIEW_THRESHOLD
    )
    assert executor._write_confidence_threshold(_action("sec32_misc_note"), normal_fid) == (
        executor.LOW_CONFIDENCE_REVIEW_THRESHOLD
    )


def test_find_ctrl_with_strategy_reports_label_and_position(monkeypatch):
    fid = {"control_type": "Edit", "name": "Authority", "top": 10, "left": 20}
    marker = object()

    monkeypatch.setattr(executor, "_label_find_edit", lambda *args, **kwargs: marker)
    monkeypatch.setattr(executor, "_find_edit_by_position", lambda *args, **kwargs: None)
    ctrl, strategy = executor._find_ctrl_with_strategy(object(), fid, delta=(0, 0), row_index=None)
    assert ctrl is marker
    assert strategy == "edit:label_proximity"

    monkeypatch.setattr(executor, "_label_find_edit", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_find_edit_by_position", lambda *args, **kwargs: marker)
    ctrl, strategy = executor._find_ctrl_with_strategy(object(), fid, delta=(0, 0), row_index=None)
    assert ctrl is marker
    assert strategy == "edit:position_fallback"


def test_execution_diagnostics_logs_jsonl():
    base = Path(r"C:\Users\moham\projects\triconvey-agent\output\test-execution-diag")
    base.mkdir(parents=True, exist_ok=True)
    diag = executor._ExecutionDiagnostics(base)
    diag.log_event("unit_test", value=123)

    assert diag.base_dir is not None
    assert diag.events_path is not None
    assert diag.base_dir.exists()
    assert diag.events_path.exists()
    assert '"kind": "unit_test"' in diag.events_path.read_text(encoding="utf-8")


def test_non_executable_brain_d_questions_are_skipped_from_plan():
    answers = {
        "sec32_oc_inactive": AnswerObject(
            question_id="sec32_oc_inactive",
            question_label="Owners Corporation is inactive",
            value=True,
            confidence=1.0,
        ),
    }
    plan = build_action_plan(answers, YAML_DIR)
    assert plan.actions == []


def test_due_diligence_field_is_read_only_in_action_plan():
    answers = {
        "policy_6_due_diligence": AnswerObject(
            question_id="policy_6_due_diligence",
            question_label="12. Due Diligence Checklist",
            value="Is attached",
            confidence=1.0,
        ),
    }

    plan = build_action_plan(answers, YAML_DIR)

    assert plan.actions == []


def test_attachments_field_is_written_to_sec32_6_action_plan():
    answers = {
        "policy_6_attachments": AnswerObject(
            question_id="policy_6_attachments",
            question_label="13. Attachments",
            value="- Due Diligence Checklist\n- Register Search Statement",
            confidence=0.9,
        ),
    }

    plan = build_action_plan(answers, YAML_DIR)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.question_id == "policy_6_attachments"
    assert action.action == "set_text"
    assert action.payload == "- Due Diligence Checklist\n- Register Search Statement"
    assert action.expected_after == "- Due Diligence Checklist\n- Register Search Statement"
    assert action.needs_review_first is False
    assert action.field_id == "Sec. 32 (6)::Edit::13. Attachments::t424l-1870"


def test_planning_certificate_checkbox_is_kept_checked_in_action_plan():
    answers = {
        "policy_2_planning_cert_attached": AnswerObject(
            question_id="policy_2_planning_cert_attached",
            question_label="Certificate with required information attached (planning scheme)",
            value=True,
            confidence=1.0,
        ),
    }

    plan = build_action_plan(answers, YAML_DIR)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.question_id == "policy_2_planning_cert_attached"
    assert action.action == "set_checkbox"
    assert action.payload is True
    assert action.field_id == "Sec. 32 (2)::CheckBox::Certificate with required information attached::t588l-1873"


def test_control_cache_reuses_descendants_until_invalidated():
    class DummyWindow:
        handle = 123

        def __init__(self):
            self.calls = 0

        def descendants(self, control_type=None):
            self.calls += 1
            return [control_type, self.calls]

    window = DummyWindow()
    executor._invalidate_control_cache()
    first = executor._descendants_cached(window, "Edit")
    second = executor._descendants_cached(window, "Edit")
    assert first == second
    assert window.calls == 1

    executor._invalidate_control_cache()
    third = executor._descendants_cached(window, "Edit")
    assert third != first
    assert window.calls == 2


def test_click_tab_falls_back_to_sec32_strip_segment(monkeypatch):
    class DummyRect:
        left = 100
        top = 40

        def width(self):
            return 600

        def height(self):
            return 30

    class DummyStrip:
        def rectangle(self):
            return DummyRect()

    clicks: list[tuple[int, int]] = []

    monkeypatch.setattr(executor, "_ensure_focus", lambda _window: None)
    monkeypatch.setattr(executor, "_find_sec32_tab_strip", lambda _window: DummyStrip())
    monkeypatch.setattr(executor, "_descendants_cached", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        executor,
        "_pw_mouse",
        type("Mouse", (), {"click": staticmethod(lambda **kwargs: clicks.append(kwargs["coords"]))}),
        raising=False,
    )

    class DummyChild:
        def exists(self, timeout=None):
            return False

    class DummyWindow:
        def child_window(self, **kwargs):
            return DummyChild()

    assert executor._click_tab(DummyWindow(), "Sec. 32 (4)") is True
    assert clicks == [(450, 56)]


def test_expected_matches_actual_normalizes_multiline_text():
    expected = "- Due Diligence Checklist\r\n- Register Search Statement"
    actual = "- Due Diligence Checklist\n- Register Search Statement"
    assert executor._expected_matches_actual(expected, actual) is True
