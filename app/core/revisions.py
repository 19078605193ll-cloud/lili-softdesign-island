from app.core.errors import fail


async def check_revision(session, batch):
    expected = session.info.get("expected_revision")
    if "audit" not in session.info or session.info.get("read_only_preview"):
        return
    if expected is None:
        raise fail(428, "REVISION_REQUIRED", "请刷新页面后再操作")
    if expected.strip('"') != str(batch.revision):
        raise fail(409, "CONTENT_CHANGED", "内容已被更新，请刷新后重试")
    if not session.info.get("revision_bumped"):
        batch.revision += 1
        session.info["revision_bumped"] = True
