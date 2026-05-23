from typing import Dict, Optional

from chat.application.tools.services.web_fetch.content_processor import ContentProcessor
from common.logger import log_fail, log_ok
from playwright.async_api import ElementHandle

from ..action_runtime import (
    action_error_response,
    get_existing_page_or_error,
    selector_or_error_response,
    session_state,
)
from ..intervention import UserInterventionDetector
from ..protocol import (
    ActionResult,
    RecoveryHint,
    build_error_response,
    build_success_response,
    get_page_state,
    make_action_failed_error,
    make_ref_not_found_error,
    make_schema_error,
    make_user_intervention_error_from_signal,
)
from ..session import BrowserSessionManager
from ..snapshot import SnapshotManager

_SETTLE_WAIT_MS = 800

_DETACHED_DOM_ERROR_PATTERNS = (
    "element is not attached to the dom",
    "element is not attached",
    "not attached to the dom",
    "detached from document",
    "element is not stable",
    "element is not visible",
)


def _is_detached_dom_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(pattern in message for pattern in _DETACHED_DOM_ERROR_PATTERNS)


async def _should_invalidate_after_fill(target) -> bool:
    try:
        data = await target.evaluate(
            """el => ({
                tag: el.tagName.toLowerCase(),
                role: (el.getAttribute('role') || '').toLowerCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                ariaAutocomplete: (el.getAttribute('aria-autocomplete') || '').toLowerCase(),
                ariaExpanded: (el.getAttribute('aria-expanded') || '').toLowerCase(),
                hasList: Boolean(el.getAttribute('list'))
            })"""
        )
    except Exception:
        return False

    role = data.get("role")
    input_type = data.get("type")
    aria_autocomplete = data.get("ariaAutocomplete")
    aria_expanded = data.get("ariaExpanded")

    return (
        role in {"searchbox", "combobox"}
        or input_type == "search"
        or aria_autocomplete in {"list", "both", "inline"}
        or aria_expanded == "true"
        or bool(data.get("hasList"))
    )


async def _resolve_ref_element_or_error(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    page,
    ref: str,
    resolved_snapshot_id: Optional[str],
) -> tuple[Optional[ElementHandle], Optional[str]]:
    try:
        target = await snapshot_manager.resolve_element(page, ref)
    except ValueError:
        return None, await action_error_response(
            session_manager,
            page,
            make_schema_error(
                f"Invalid ref '{ref}'. Ref must be an exact id from the latest snapshot, such as 'e1', 'e2', 'e123'. "
                f"Do NOT use role names like 'searchbox', 'button', 'link', 'textbox' or labels like 'Search'."
            ),
        )

    if target is None:
        page_state = await get_page_state(page)
        return None, build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_ref_not_found_error(ref, resolved_snapshot_id),
        )

    return target, None


async def _resolve_fill_target(target: ElementHandle):
    try:
        if await target.is_editable():
            return target
    except Exception:
        return None

    return None


async def _resolve_select_target(target: ElementHandle):
    try:
        data = await target.evaluate(
            """el => ({
                tag: el.tagName.toLowerCase(),
                disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true'
            })"""
        )
        if data["tag"] == "select" and not data["disabled"]:
            return target
    except Exception:
        return None

    return None


async def _resolve_check_target(target: ElementHandle):
    try:
        data = await target.evaluate(
            """el => ({
                tag: el.tagName.toLowerCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                role: (el.getAttribute('role') || '').toLowerCase(),
                disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
                hasLabelControl: Boolean(
                    (el.tagName.toUpperCase() === 'LABEL' ? el : el.closest && el.closest('label'))?.control
                ),
                hasNestedCheckInput: Boolean(el.querySelector && el.querySelector('input[type="checkbox"], input[type="radio"]'))
            })"""
        )

        if data["disabled"]:
            return None

        if data["tag"] == "input" and data["type"] in ("checkbox", "radio"):
            return target

        if data["role"] in ("checkbox", "radio"):
            return target

        if data["hasLabelControl"] or data["hasNestedCheckInput"]:
            return target
    except Exception:
        return None

    return None


async def _is_checked(target: ElementHandle) -> bool:
    try:
        return bool(
            await target.evaluate(
                """el => {
                    const tag = el.tagName && el.tagName.toUpperCase();
                    if (tag === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
                        return el.checked;
                    }
                    const role = el.getAttribute && (el.getAttribute('role') || '').toLowerCase();
                    const ariaCheckedRoles = ['checkbox','radio','switch','menuitemcheckbox','menuitemradio','option','treeitem'];
                    if (role && ariaCheckedRoles.includes(role)) {
                        return el.getAttribute('aria-checked') === 'true';
                    }
                    const label = tag === 'LABEL' ? el : (el.closest && el.closest('label'));
                    if (label && label.tagName && label.tagName.toUpperCase() === 'LABEL' && label.control) {
                        const control = label.control;
                        if (control.type === 'checkbox' || control.type === 'radio') {
                            return control.checked;
                        }
                    }
                    const input = el.querySelector && el.querySelector('input[type="checkbox"], input[type="radio"]');
                    if (input) return input.checked;
                    return false;
                }"""
            )
        )
    except Exception:
        return False


async def _js_click_check_target(target: ElementHandle) -> None:
    await target.evaluate(
        """el => {
            const tag = el.tagName && el.tagName.toUpperCase();
            if (tag === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
                el.click();
                return;
            }
            const label = tag === 'LABEL' ? el : (el.closest && el.closest('label'));
            if (label && label.tagName && label.tagName.toUpperCase() === 'LABEL' && label.control) {
                label.control.click();
                return;
            }
            const input = el.querySelector && el.querySelector('input[type="checkbox"], input[type="radio"]');
            if (input) {
                input.click();
                return;
            }
            el.click();
        }"""
    )


async def handle_click_ref(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    snapshot_id = action.get("snapshot_id")
    ref = action.get("ref")

    if not ref:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("click_ref requires a 'ref' field."),
        )

    resolved_snapshot_id, snap_error = snapshot_manager.resolve_current(snapshot_id)
    if snap_error:
        return await action_error_response(session_manager, page, snap_error)

    parsed_ref, selector_error_response = await selector_or_error_response(
        session_manager,
        page,
        ref,
    )
    if selector_error_response:
        return selector_error_response

    opened_new_page = False

    target = None
    try:
        pages_before = list(page.context.pages)
        target, ref_error_response = await _resolve_ref_element_or_error(
            session_manager,
            snapshot_manager,
            page,
            parsed_ref,
            resolved_snapshot_id,
        )
        if ref_error_response:
            return ref_error_response

        await target.scroll_into_view_if_needed()
        await target.click()
        await page.wait_for_timeout(_SETTLE_WAIT_MS)

        pages_after = list(page.context.pages)
        new_pages = [
            candidate for candidate in pages_after if candidate not in pages_before
        ]
        if new_pages:
            page = new_pages[-1]
            session_manager.set_current_page(page)
            opened_new_page = True
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
    except Exception as error:
        log_fail("浏览器点击", str(error))
        page_state = await get_page_state(page)

        if _is_detached_dom_error(error):
            return build_error_response(
                session_state=session_state(session_manager),
                page_state=page_state,
                error=make_action_failed_error(
                    action_type="click_ref",
                    message="Click target changed in the DOM. Take a fresh snapshot before retrying.",
                    diagnostic_code="CLICK_TARGET_DETACHED",
                    retryable=True,
                    context={
                        "ref": ref,
                        "snapshot_id": resolved_snapshot_id,
                    },
                    recovery_hint=RecoveryHint(
                        type="refresh_snapshot",
                        required_before_retry=True,
                        reason="The target element changed, detached, or became unstable. Take a new snapshot before retrying.",
                    ),
                ),
            )

        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="click_ref",
                message=f"Click failed: {error}",
                diagnostic_code="CLICK_FAILED",
                context={"ref": ref, "snapshot_id": resolved_snapshot_id},
            ),
        )
    finally:
        if target is not None:
            await target.dispose()

    snapshot_manager.invalidate()

    intervention_error = await intervention.detect(page)
    if intervention_error:
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_user_intervention_error_from_signal(intervention_error),
        )

    page_state = await get_page_state(page)
    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="click_ref",
            status="completed",
            detail={
                "ref": ref,
                "snapshot_id": resolved_snapshot_id,
                "opened_new_page": opened_new_page,
                "refs_invalidated": True,
            },
        ),
    )


async def handle_fill_ref(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    snapshot_id = action.get("snapshot_id")
    ref = action.get("ref")

    if "text" not in action:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("fill_ref requires a 'text' field."),
        )

    text = action.get("text", "")

    if not ref:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("fill_ref requires a 'ref' field."),
        )

    resolved_snapshot_id, snap_error = snapshot_manager.resolve_current(snapshot_id)
    if snap_error:
        return await action_error_response(session_manager, page, snap_error)

    parsed_ref, selector_error_response = await selector_or_error_response(
        session_manager,
        page,
        ref,
    )
    if selector_error_response:
        return selector_error_response

    target = None
    try:
        target, ref_error_response = await _resolve_ref_element_or_error(
            session_manager,
            snapshot_manager,
            page,
            parsed_ref,
            resolved_snapshot_id,
        )
        if ref_error_response:
            return ref_error_response

        fill_target = await _resolve_fill_target(target)
        if fill_target is None:
            page_state = await get_page_state(page)
            return build_error_response(
                session_state=session_state(session_manager),
                page_state=page_state,
                error=make_action_failed_error(
                    action_type="fill_ref",
                    message="The referenced element is not fillable.",
                    diagnostic_code="REF_NOT_FILLABLE",
                    context={"ref": ref, "snapshot_id": resolved_snapshot_id},
                ),
            )

        await fill_target.scroll_into_view_if_needed()
        refs_invalidated = await _should_invalidate_after_fill(fill_target)
        await fill_target.fill(text)

        if refs_invalidated:
            snapshot_manager.invalidate()
    except Exception as error:
        log_fail("浏览器填充", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="fill_ref",
                message=f"Fill failed: {error}",
                diagnostic_code="FILL_FAILED",
                context={"ref": ref, "snapshot_id": resolved_snapshot_id},
            ),
        )
    finally:
        if target is not None:
            await target.dispose()

    page_state = await get_page_state(page)
    text_length = len(text)
    log_ok("浏览器填充", ref=ref, text_length=text_length)

    detail: Dict = {
        "ref": ref,
        "snapshot_id": resolved_snapshot_id,
        "text_length": text_length,
    }
    if refs_invalidated:
        detail["refs_invalidated"] = True

    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="fill_ref",
            status="completed",
            detail=detail,
        ),
    )


async def handle_select_ref(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    snapshot_id = action.get("snapshot_id")
    ref = action.get("ref")
    value = action.get("value")
    label = action.get("label")

    if not ref:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("select_ref requires a 'ref' field."),
        )

    if value is None and label is None:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("select_ref requires a 'value' or 'label' field."),
        )

    resolved_snapshot_id, snap_error = snapshot_manager.resolve_current(snapshot_id)
    if snap_error:
        return await action_error_response(session_manager, page, snap_error)

    parsed_ref, selector_error_response = await selector_or_error_response(
        session_manager,
        page,
        ref,
    )
    if selector_error_response:
        return selector_error_response

    target = None
    try:
        target, ref_error_response = await _resolve_ref_element_or_error(
            session_manager,
            snapshot_manager,
            page,
            parsed_ref,
            resolved_snapshot_id,
        )
        if ref_error_response:
            return ref_error_response

        select_target = await _resolve_select_target(target)
        if select_target is None:
            page_state = await get_page_state(page)
            return build_error_response(
                session_state=session_state(session_manager),
                page_state=page_state,
                error=make_action_failed_error(
                    action_type="select_ref",
                    message="The referenced element is not selectable.",
                    diagnostic_code="REF_NOT_SELECTABLE",
                    context={"ref": ref, "snapshot_id": resolved_snapshot_id},
                ),
            )
        await select_target.scroll_into_view_if_needed()

        if value is not None:
            selected = await select_target.select_option(value=value)
        else:
            selected = await select_target.select_option(label=label)

        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        log_fail("浏览器选择", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="select_ref",
                message=f"Select failed: {error}",
                diagnostic_code="SELECT_FAILED",
                context={"ref": ref, "snapshot_id": resolved_snapshot_id},
            ),
        )
    finally:
        if target is not None:
            await target.dispose()

    snapshot_manager.invalidate()

    intervention_error = await intervention.detect(page)
    if intervention_error:
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_user_intervention_error_from_signal(intervention_error),
        )

    page_state = await get_page_state(page)
    log_ok("浏览器选择", ref=ref)

    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="select_ref",
            status="completed",
            detail={
                "ref": ref,
                "snapshot_id": resolved_snapshot_id,
                "selected_count": len(selected),
                "refs_invalidated": True,
            },
        ),
    )


async def handle_check_ref(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    snapshot_id = action.get("snapshot_id")
    ref = action.get("ref")
    checked = action.get("checked", True)

    if not ref:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("check_ref requires a 'ref' field."),
        )

    resolved_snapshot_id, snap_error = snapshot_manager.resolve_current(snapshot_id)
    if snap_error:
        return await action_error_response(session_manager, page, snap_error)

    parsed_ref, selector_error_response = await selector_or_error_response(
        session_manager,
        page,
        ref,
    )
    if selector_error_response:
        return selector_error_response

    target = None
    try:
        target, ref_error_response = await _resolve_ref_element_or_error(
            session_manager,
            snapshot_manager,
            page,
            parsed_ref,
            resolved_snapshot_id,
        )
        if ref_error_response:
            return ref_error_response

        check_target = await _resolve_check_target(target)
        if check_target is None:
            page_state = await get_page_state(page)
            return build_error_response(
                session_state=session_state(session_manager),
                page_state=page_state,
                error=make_action_failed_error(
                    action_type="check_ref",
                    message="The referenced element is not checkable.",
                    diagnostic_code="REF_NOT_CHECKABLE",
                    context={"ref": ref, "snapshot_id": resolved_snapshot_id},
                ),
            )
        await check_target.scroll_into_view_if_needed()

        current_checked = await _is_checked(check_target)
        if checked != current_checked:
            try:
                await check_target.click()
            except Exception:
                await _js_click_check_target(check_target)

            if await _is_checked(check_target) != checked:
                await _js_click_check_target(check_target)

        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        log_fail("浏览器勾选", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="check_ref",
                message=f"Check failed: {error}",
                diagnostic_code="CHECK_FAILED",
                context={"ref": ref, "snapshot_id": resolved_snapshot_id},
            ),
        )
    finally:
        if target is not None:
            await target.dispose()

    snapshot_manager.invalidate()

    intervention_error = await intervention.detect(page)
    if intervention_error:
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_user_intervention_error_from_signal(intervention_error),
        )

    page_state = await get_page_state(page)
    log_ok("浏览器勾选", ref=ref, checked=checked)

    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="check_ref",
            status="completed",
            detail={
                "ref": ref,
                "snapshot_id": resolved_snapshot_id,
                "checked": checked,
                "refs_invalidated": True,
            },
        ),
    )
