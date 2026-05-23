import asyncio

from playwright.async_api import async_playwright

from chat.application.tools.services.browser_interact.core.snapshot import (
    CursorElementInfo,
    RefMap,
    RefEntry,
    SnapshotManager,
    SnapshotNode,
    assign_refs,
    focused_elements,
    format_snapshot_tree,
    parse_ref,
    promote_hidden_inputs,
)


def test_parse_ref_accepts_agent_browser_forms():
    assert parse_ref("e1") == "e1"
    assert parse_ref("@e12") == "e12"
    assert parse_ref("ref=e7") == "e7"
    assert parse_ref("button") is None


def test_assign_refs_tracks_duplicate_role_name_nth():
    nodes = [
        SnapshotNode(role="button", name="Buy", backend_node_id=1),
        SnapshotNode(role="button", name="Buy", backend_node_id=2),
        SnapshotNode(role="textbox", name="Search", backend_node_id=3),
    ]

    assign_refs(nodes, {})

    assert [node.ref for node in nodes] == ["e1", "e2", "e3"]
    assert nodes[0].nth == 0
    assert nodes[1].nth == 1
    assert nodes[2].nth is None


def test_promote_hidden_radio_label_to_checkable_ref():
    nodes = [SnapshotNode(role="LabelText", name="", backend_node_id=10)]
    cursor_elements = {
        10: CursorElementInfo(
            kind="clickable",
            hints=["cursor:pointer"],
            text="Single unit",
            hidden_input_kind="radio",
            hidden_input_checked="false",
        )
    }

    promote_hidden_inputs(nodes, cursor_elements)

    assert nodes[0].role == "radio"
    assert nodes[0].name == "Single unit"
    assert nodes[0].checked == "false"


def test_focused_snapshot_resets_indent_for_ranked_subset():
    elements = [
        {
            "ref": "e1",
            "role": "button",
            "label": "Later",
            "depth": 3,
            "clickable": True,
        },
        {
            "ref": "e2",
            "role": "searchbox",
            "label": "Search",
            "depth": 4,
            "fillable": True,
        },
    ]

    focused = [{**element, "depth": 0} for element in focused_elements(elements, "search", 1)]
    tree = format_snapshot_tree(focused)

    assert tree.startswith('- searchbox "Search"')
    assert "ref=e2" in tree


def test_ref_map_sorts_numeric_refs():
    ref_map = RefMap()
    ref_map.add(RefEntry(ref="e10", role="button", name="Ten"))
    ref_map.add(RefEntry(ref="e2", role="button", name="Two"))

    assert [ref for ref, _ in ref_map.entries_sorted()] == ["e2", "e10"]


def test_ref_map_filtered_keeps_only_visible_focused_refs():
    ref_map = RefMap()
    ref_map.add(RefEntry(ref="e1", role="button", name="One"))
    ref_map.add(RefEntry(ref="e2", role="button", name="Two"))

    filtered = ref_map.filtered({"e2"})

    assert filtered.get("e1") is None
    assert filtered.get("e2").name == "Two"


def test_snapshot_expands_same_origin_iframe_and_resolves_ref():
    async def run() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.set_content(
                    """
                    <!doctype html>
                    <html><body>
                      <button>Main</button>
                      <iframe srcdoc="<button>Inner</button><input aria-label='Inside' />"></iframe>
                    </body></html>
                    """
                )
                await page.wait_for_timeout(500)

                manager = SnapshotManager()
                snapshot = await manager.take(page)

                assert '- button "Inner" [ref=e3]' in snapshot.tree
                assert '- textbox "Inside" [ref=e4, flags=fillable]' in snapshot.tree

                target = await manager.resolve_element(page, "e4")
                assert target is not None
                try:
                    await target.fill("frame-ok")
                    assert (
                        await page.frame_locator("iframe")
                        .locator('[aria-label="Inside"]')
                        .input_value()
                        == "frame-ok"
                    )
                finally:
                    await target.dispose()
            finally:
                await browser.close()

    asyncio.run(run())
