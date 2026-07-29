"""GET /langchain/mcp-tools must mark each tool as foundational or not, per
the single shared selection in helpers/foundational_tools.py — see the
refactor-foundational-tool-list OpenSpec change."""

from types import SimpleNamespace


def _fake_tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=f"{name} description")


def test_foundational_field_matches_shared_selection(client, monkeypatch):
    import deps

    monkeypatch.setattr(
        deps,
        "mcp_tools",
        [
            _fake_tool("list_available_experiments"),
            _fake_tool("load_experiment_data"),
            _fake_tool("list_existing_analyses"),
            _fake_tool("qc_clean"),
            _fake_tool("pca_analysis"),
        ],
    )

    response = client.get(
        "/langchain/mcp-tools", headers={"Authorization": "Bearer test"}
    )
    assert response.status_code == 200

    tools = {t["name"]: t for t in response.json()["tools"]}
    assert tools["list_available_experiments"]["foundational"] is True
    assert tools["load_experiment_data"]["foundational"] is True
    assert tools["list_existing_analyses"]["foundational"] is True
    assert tools["qc_clean"]["foundational"] is False
    assert tools["pca_analysis"]["foundational"] is False


def test_foundational_field_is_prefix_aware_for_namespaced_tools(client, monkeypatch):
    import deps

    monkeypatch.setattr(
        deps,
        "mcp_tools",
        [
            _fake_tool("core_list_available_experiments"),
            _fake_tool("sleap_roots_qc_clean"),
        ],
    )

    response = client.get(
        "/langchain/mcp-tools", headers={"Authorization": "Bearer test"}
    )
    tools = {t["name"]: t for t in response.json()["tools"]}
    assert tools["core_list_available_experiments"]["foundational"] is True
    assert tools["sleap_roots_qc_clean"]["foundational"] is False
