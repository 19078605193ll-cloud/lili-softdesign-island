import io
import json
import uuid
import zipfile

import pytest
from PIL import Image
from sqlalchemy import event, select

from app.imports.api import get_import_storage
from app.imports.storage import LocalImportStorage
from app.main import app
from app.models import (KnowledgeNode, PracticeQuestionRevision, Question,
                        QuestionImportBatch, QuestionImportGroup, QuestionSourceDocument)
from app.imports.markdown_workflow import refresh_rule_drafts

pytestmark = pytest.mark.integration

SOURCE = '17-18. 共享材料\nA.a B.b C.c D.d\nA.e B.f C.g D.h\n答案：A B\n解析：共同解析\n\n99. 独立题\nA.a B.b C.c D.d\n答案：C\n解析：独立解析'


@pytest.fixture(autouse=True)
def disable_automatic_external_ai(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), 'ai_api_key', '')


async def upload(client, source=SOURCE, code=None, filename='paper.md', data=None):
    response = await client.post('/api/v1/admin/import-batches', data=dict(year=2025,period='first_half',
        title='回忆版测试',batch_code=code or uuid.uuid4().hex), files={'file':(filename, data or source.encode(), 'application/octet-stream')})
    assert response.status_code == 201, response.text
    return response.json()['id']


async def review(client, session, batch):
    state=(await client.get('/api/v1/admin/markdown-batches/'+batch)).json()
    topic=await session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_type=='topic',KnowledgeNode.status=='active'))
    for block in state['blocks']:
        response=await client.post(f'/api/v1/admin/markdown-batches/{batch}/blocks/{block["id"]}/review',json={
            'action':'approve','selections':{str(p['question_no']):{'primary_code':topic.code} for p in block['parts']}})
        assert response.status_code==200,response.text
    return state


@pytest.fixture
def storage(tmp_path):
    value=LocalImportStorage(tmp_path)
    app.dependency_overrides[get_import_storage]=lambda:value
    yield value
    app.dependency_overrides.pop(get_import_storage,None)


async def test_composite_publish_read_score_duplicate_and_replace(client,session,storage):
    code=uuid.uuid4().hex
    batch=await upload(client,code=code)
    state=await review(client,session,batch)
    assert len(state['blocks'])==2 and len(state['blocks'][0]['parts'])==2
    result=await client.post(f'/api/v1/admin/markdown-batches/{batch}/publish')
    assert result.status_code==200,result.text
    publication=result.json()
    assert publication['question_count']==2 and publication['subquestion_count']==3
    again=await client.post(f'/api/v1/admin/markdown-batches/{batch}/publish')
    assert again.json()['already_published']
    units=(await client.get('/api/v1/papers/'+publication['paper_id']+'/practice-questions')).json()
    composite=next(u for u in units if u['type']=='composite')
    assert len(composite['parts'])==2
    assert 'correct_option_keys' not in str(composite) and 'explanation' not in str(composite)
    answers={p['id']:a for p,a in zip(composite['parts'],['A','B'])}
    checked=await client.post('/api/v1/practice-questions/'+composite['id']+'/check',json={'answers':answers})
    assert float(checked.json()['score'])==2
    missing=await client.post('/api/v1/practice-questions/'+composite['id']+'/check',json={'answers':{}})
    assert missing.status_code==422
    duplicate=await upload(client,code=code)
    await review(client,session,duplicate)
    result=await client.post(f'/api/v1/admin/markdown-batches/{duplicate}/publish')
    assert result.status_code==200,result.text
    assert result.json()['skipped']==2 and result.json()['question_count']==2
    changed=await upload(client,source=SOURCE.replace('共同解析','修订解析'),code=code)
    await review(client,session,changed)
    result=await client.post(f'/api/v1/admin/markdown-batches/{changed}/publish')
    assert result.status_code==409,result.text
    conflicts=(await client.get(f'/api/v1/admin/markdown-batches/{changed}/conflicts')).json()
    conflict=next(c for c in conflicts if not c['identical'])
    assert not conflict['boundary_conflict']
    await client.post(f'/api/v1/admin/markdown-batches/{changed}/blocks/{conflict["block_id"]}/conflict',
        json={'action':'replace','old_hash':conflict['old_hash']})
    result=await client.post(f'/api/v1/admin/markdown-batches/{changed}/publish')
    assert result.status_code==200,result.text
    assert result.json()['replaced']==1
    revised=(await client.get('/api/v1/practice-questions/'+composite['id']+'/solution')).json()
    assert revised['explanation_markdown']=='修订解析'
    assert revised['parts'][0]['explanation_markdown']==''
    assert await session.scalar(select(PracticeQuestionRevision.id).where(PracticeQuestionRevision.batch_id==uuid.UUID(changed)))


async def test_rule_refresh_changes_only_unreviewed_draft(client, session, storage):
    batch_id = await upload(client)
    state = (await client.get(f'/api/v1/admin/markdown-batches/{batch_id}')).json()
    first, second = state['blocks']
    topic = await session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_type == 'topic'))
    approved = await client.post(f'/api/v1/admin/markdown-batches/{batch_id}/blocks/{first["id"]}/review', json={
        'action': 'approve', 'selections': {str(p['question_no']): {'primary_code': topic.code} for p in first['parts']}})
    assert approved.status_code == 200, approved.text
    batch = await session.get(QuestionImportBatch, uuid.UUID(batch_id))
    doc = await session.scalar(select(QuestionSourceDocument).where(QuestionSourceDocument.batch_id == batch.id))
    first_group = await session.get(QuestionImportGroup, uuid.UUID(first['group_id']))
    second_group = await session.get(QuestionImportGroup, uuid.UUID(second['group_id']))
    first_original = first_group.material_markdown
    second_group.material_markdown = 'outdated parser content'
    await session.commit()
    changed = await refresh_rule_drafts(session, batch, doc)
    await session.commit()
    assert changed == 1
    assert first_group.material_markdown == first_original
    assert second_group.material_markdown == 'outdated parser content'
    assert doc.extracted_content['blocks'][1]['repair_suggestion']


async def test_images_exclusion_and_supplement(client,session,storage):
    image=io.BytesIO();Image.new('RGB',(8,8),'white').save(image,format='PNG')
    package=io.BytesIO()
    source=SOURCE.replace('共享材料','共享材料\n![](D:\\软考真题\\images\\fig.png)').replace('独立题','独立题\n![](missing.png)')
    with zipfile.ZipFile(package,'w') as z:
        z.writestr('paper.md',source);z.writestr('images/fig.png',image.getvalue())
    batch=await upload(client,filename='paper.zip',data=package.getvalue())
    state=(await client.get('/api/v1/admin/markdown-batches/'+batch)).json()
    assert 'missing.png' in state['image_errors']
    group=state['blocks'][0]; bad=state['blocks'][1]
    assert 'asset://' in group['material_markdown']
    asset_id=group['material_markdown'].split('asset://')[1].split(')')[0]
    assert (await client.get('/api/v1/question-assets/'+asset_id)).status_code==404
    topic=await session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_type=='topic'))
    result=await client.post(f'/api/v1/admin/markdown-batches/{batch}/blocks/{group["id"]}/review',json={
        'action':'approve','selections':{str(p['question_no']):{'primary_code':topic.code} for p in group['parts']}})
    assert result.status_code==200,result.text
    result=await client.post(f'/api/v1/admin/markdown-batches/{batch}/blocks/{bad["id"]}/review',json={'action':'exclude','reason':'待补图'})
    assert result.status_code==200,result.text
    result=await client.post(f'/api/v1/admin/markdown-batches/{batch}/publish')
    assert result.status_code==200,result.text
    assert result.json()['question_count']==1 and result.json()['subquestion_count']==2
    assert (await client.get('/api/v1/question-assets/'+asset_id)).status_code==200
    followup=await client.post(f'/api/v1/admin/markdown-batches/{batch}/supplement')
    assert followup.status_code==200,followup.text
    next_id=followup.json()['batch_id']
    fixed=await client.post(f'/api/v1/admin/markdown-batches/{next_id}/images',data={'reference':'missing.png'},files={'file':('x.png',image.getvalue(),'image/png')})
    assert fixed.status_code==200,fixed.text
    await review(client,session,next_id)
    result=await client.post(f'/api/v1/admin/markdown-batches/{next_id}/publish')
    assert result.status_code==200,result.text
    assert result.json()['question_count']==2


async def test_zip_traversal_and_retired_upload(client,storage):
    package=io.BytesIO()
    with zipfile.ZipFile(package,'w') as z:
        z.writestr('../escape.md',SOURCE)
    result=await client.post('/api/v1/admin/import-batches',files={'file':('bad.zip',package.getvalue())})
    assert result.status_code==422,result.text
    result=await client.post('/api/v1/admin/import-batches',files={'file':('old.pdf',b'%PDF-1.7')})
    assert result.status_code==422
    result=await client.post('/api/v1/admin/import-batches/'+str(uuid.uuid4())+'/parse')
    assert result.status_code==410


async def test_preview_sanitizes_html_and_hides_external_images(client,storage):
    batch=await upload(client)
    result=await client.post(f'/api/v1/admin/markdown-batches/{batch}/preview',json={'markdown':'<script>alert(1)</script><img src="http://localhost/a" onerror="alert(1)">\n![](http://example.com/a.png)'})
    assert result.status_code==200,result.text
    html=result.json()['html']
    assert '<script' not in html and 'onerror' not in html and 'src="http' not in html


async def test_text_assist_sanitizes_null_and_preserves_review_gate(client,storage,monkeypatch):
    from types import SimpleNamespace
    from app.imports import markdown_api
    batch=await upload(client,source='1. 问题\nA.a B.b C.c D.d\n答案：A\n解析：内容')
    state=(await client.get('/api/v1/admin/markdown-batches/'+batch)).json()
    block=state['blocks'][0]
    response_data=dict(source_label='1',material_markdown='',parts=[dict(question_no=1,
        stem_markdown='问题\x00',options=[dict(key=k,content_markdown=k.lower()) for k in 'ABCD'],
        correct_option_keys=['A'],explanation_markdown='内容')])
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return False
        def __init__(self,*args,**kwargs):
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        async def create(self,**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response_data,ensure_ascii=False)))])
    monkeypatch.setattr('openai.AsyncOpenAI',FakeClient)
    monkeypatch.setattr(markdown_api,'get_settings',lambda:SimpleNamespace(ai_api_key='fake',ai_text_model='fake',ai_classification_model=None,ai_base_url=None,ai_timeout_seconds=1))
    result=await client.post(f'/api/v1/admin/markdown-batches/{batch}/blocks/{block["id"]}/assist')
    assert result.status_code==200,result.text
    assert any('控制字符' in issue for issue in result.json()['issues'])
    state=(await client.get('/api/v1/admin/markdown-batches/'+batch)).json()
    assert state['blocks'][0]['confirmed'] is False


async def seed_review_candidates(session, batch_id):
    from app.models import QuestionImportItem, QuestionClassificationRun, QuestionClassificationCandidate, KnowledgeTaxonomyRelease
    from app.imports.service import classification_fingerprint
    batch = await session.get(QuestionImportBatch, uuid.UUID(batch_id))
    topic = await session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_type == 'topic', KnowledgeNode.status == 'active'))
    items = list(await session.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id).order_by(QuestionImportItem.question_no)))
    release = await session.get(KnowledgeTaxonomyRelease, batch.taxonomy_release_id)
    for item in items:
        group = await session.get(QuestionImportGroup, item.group_id)
        run = QuestionClassificationRun(import_item_id=item.id, taxonomy_release_id=batch.taxonomy_release_id,
            provider='fake', model='fake', prompt_version='test', catalog_checksum=release.checksum_sha256, input_fingerprint=classification_fingerprint(item, group), status='completed')
        session.add(run)
        await session.flush()
        session.add(QuestionClassificationCandidate(run_id=run.id, knowledge_node_id=topic.id,
            role='primary', rank=1, confidence=0.9, rationale='test'))
    await session.commit()
    return items, topic


async def test_markdown_list_detail_and_approval_preview(client, session, storage):
    batch = await upload(client)
    base = '/api/v1/admin/markdown-batches/' + batch
    state = (await client.get(base)).json()
    assert all(not b['approval']['eligible'] for b in state['blocks'])
    assert any('主知识点' in r for r in state['blocks'][0]['approval']['reasons'])
    items, topic = await seed_review_candidates(session, batch)
    state = (await client.get(base)).json()
    assert all(b['approval']['eligible'] for b in state['blocks'])
    listing = await client.get('/admin/imports/' + batch)
    assert listing.status_code == 200
    assert 'const initialBlock=null' in listing.text
    assert '<section class="batch-tools' in listing.text
    assert '<details class="batch-tools' not in listing.text
    detail = await client.get('/admin/imports/' + batch + '/blocks/' + state['blocks'][0]['id'])
    assert detail.status_code == 200 and '返回题目列表' in detail.text
    assert '@media(min-width:981px){.source-pane{position:sticky;top:12px' in detail.text
    assert 'max-height:calc(100vh - 24px);overflow:hidden' in detail.text
    assert 'overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable' in detail.text
    assert '@media(max-width:980px)' in detail.text
    assert '.source-pane{position:static;display:block;max-height:none}' in detail.text
    assert (await client.get('/admin/imports/' + batch + '/blocks/missing')).status_code == 404
    empty = await client.post(base + '/approve-ready', json={'block_ids': []})
    assert empty.json()['approved'] == empty.json()['skipped'] == 0
    chosen = state['blocks'][0]['id']
    result = await client.post(base + '/approve-ready', json={'block_ids': [chosen, chosen, 'foreign']})
    assert result.status_code == 200, result.text
    assert result.json()['approved'] == 1 and result.json()['skipped'] == 1
    after = (await client.get(base)).json()
    assert after['blocks'][0]['confirmed'] and not after['blocks'][1]['confirmed']
    assert all(i['status'] == 'approved' for i in after['blocks'][0]['items'])
    assert after['status'] != 'published'
    retry = await client.post(base + '/approve-ready', json={'block_ids': [chosen]})
    assert retry.json()['approved'] == 0
    legacy = await client.post(base + '/approve-ready')
    assert legacy.json()['approved'] == 1


async def test_markdown_batch_detail_uses_fixed_query_count(client, session, storage):
    batch = await upload(client)
    await seed_review_candidates(session, batch)
    query_count = 0

    def count_query(*_args):
        nonlocal query_count
        query_count += 1

    event.listen(session.bind.sync_engine, 'before_cursor_execute', count_query)
    try:
        response = await client.get('/api/v1/admin/markdown-batches/' + batch)
    finally:
        event.remove(session.bind.sync_engine, 'before_cursor_execute', count_query)

    assert response.status_code == 200, response.text
    assert query_count <= 10


async def test_markdown_bulk_revalidates_whole_composite(client, session, storage):
    batch = await upload(client)
    items, _ = await seed_review_candidates(session, batch)
    base = '/api/v1/admin/markdown-batches/' + batch
    before = (await client.get(base)).json()
    assert before['blocks'][0]['approval']['eligible']
    # The list was read before a second editor invalidated one child.
    items[1].correct_option_keys = []
    await session.commit()
    result = await client.post(base + '/approve-ready', json={'block_ids': [b['id'] for b in before['blocks']]})
    assert result.status_code == 200, result.text
    assert result.json()['approved'] == 1 and result.json()['skipped'] == 1
    after = (await client.get(base)).json()
    assert not after['blocks'][0]['confirmed']
    assert all(i['status'] != 'approved' for i in after['blocks'][0]['items'])
    assert after['blocks'][1]['confirmed']


async def test_markdown_saved_selection_preferred_and_image_blocked(client, session, storage):
    from app.models import QuestionImportKnowledgeSelection
    batch = await upload(client)
    items, ai_topic = await seed_review_candidates(session, batch)
    saved_topic = await session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_type == 'topic',
        KnowledgeNode.status == 'active', KnowledgeNode.id != ai_topic.id))
    session.add(QuestionImportKnowledgeSelection(import_item_id=items[0].id,
        knowledge_node_id=saved_topic.id, role='primary', source='manual'))
    items[-1].stem_markdown += '\n![](https://example.com/missing.png)'
    await session.commit()
    base = '/api/v1/admin/markdown-batches/' + batch
    state = (await client.get(base)).json()
    knowledge = state['blocks'][0]['approval']['knowledge'][0]
    assert knowledge['source'] == 'saved' and knowledge['primary_code'] == saved_topic.code
    assert any('图片' in r for r in state['blocks'][1]['approval']['reasons'])
    result = await client.post(base + '/approve-ready', json={})
    assert result.json()['approved'] == 1 and result.json()['skipped'] == 1


async def test_markdown_expected_validation_skip_and_system_rollback(client, session, storage, monkeypatch):
    from app.imports import markdown_api
    from app.imports.service import ImportWorkflowError
    from app.models import QuestionImportItem
    batch = await upload(client)
    items, _ = await seed_review_candidates(session, batch)
    base = '/api/v1/admin/markdown-batches/' + batch
    original = markdown_api._validate_approval_selection
    async def validation(session, item, batch, selection):
        if item.question_no == 18:
            raise ImportWorkflowError('知识点已失效')
        return await original(session, item, batch, selection)
    monkeypatch.setattr(markdown_api, '_validate_approval_selection', validation)
    result = await client.post(base + '/approve-ready')
    assert result.status_code == 200, result.text
    assert result.json()['approved'] == 1 and result.json()['skipped'] == 1
    rows = list(await session.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == uuid.UUID(batch)).order_by(QuestionImportItem.question_no)))
    assert [i.status for i in rows] == ['needs_review', 'needs_review', 'approved']
    monkeypatch.setattr(markdown_api, '_validate_approval_selection', original)
    other = await upload(client)
    await seed_review_candidates(session, other)
    original_apply = markdown_api._apply_approval_selection
    async def failing_apply(session, item, *args):
        if item.question_no == 99:
            raise RuntimeError('simulated database failure')
        await original_apply(session, item, *args)
    monkeypatch.setattr(markdown_api, '_apply_approval_selection', failing_apply)
    result = await client.post('/api/v1/admin/markdown-batches/' + other + '/approve-ready')
    assert result.status_code >= 400
    after = (await client.get('/api/v1/admin/markdown-batches/' + other)).json()
    assert all(not b['confirmed'] for b in after['blocks'])
    assert all(i['status'] == 'needs_review' for b in after['blocks'] for i in b['items'])


async def test_edit_preserves_ids_and_unaffected_knowledge(client, session, storage):
    from app.models import QuestionClassificationRun
    batch = await upload(client)
    items, topic = await seed_review_candidates(session, batch)
    state = (await client.get('/api/v1/admin/markdown-batches/'+batch)).json()
    block = state['blocks'][0]
    ids = [i['id'] for i in block['items']]
    unit = {k:block[k] for k in ('source_label','material_markdown','explanation_markdown','parts')}
    unit['parts'][0]['options'][0]['content_markdown'] = 'changed'
    response = await client.put(f'/api/v1/admin/markdown-batches/{batch}/blocks/{block["id"]}', json={'unit':unit})
    assert response.status_code == 200, response.text
    updated = (await client.get('/api/v1/admin/markdown-batches/'+batch)).json()['blocks'][0]
    assert [i['id'] for i in updated['items']] == ids
    assert not updated['items'][0]['candidates']
    assert updated['items'][1]['candidates']
    assert updated['items'][0]['classification_status'] == '推荐已过期'
    assert await session.scalar(select(QuestionClassificationRun.id).where(QuestionClassificationRun.import_item_id == uuid.UUID(ids[0])))


async def test_targeted_classification_force_and_history(client, session, storage, monkeypatch):
    from app.imports import markdown_api
    from app.imports.schemas import AIClassificationBatch
    from app.models import QuestionClassificationRun
    from sqlalchemy import func
    batch = await upload(client)
    topic = await session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_type=='topic'))
    calls = []
    class FakeAI:
        provider_name = 'fake'
        classification_model = 'fake'
        async def classify_questions(self, items, catalog):
            calls.append(items)
            return AIClassificationBatch.model_validate({'results':[{'question_no':i['question_no'], 'primary_candidates':[{'code':topic.code,'confidence':0.9,'rationale':'依据本小问选项'}], 'related_candidates':[]} for i in items]}), {}
    monkeypatch.setattr('app.imports.ai.OpenAICompatibleQuestionClient', lambda settings:FakeAI())
    state = (await client.get('/api/v1/admin/markdown-batches/'+batch)).json()
    block = state['blocks'][0]
    url = '/api/v1/admin/markdown-batches/'+batch+'/classify'
    payload = {'block_id':block['id'],'question_no':17}
    result = await client.post(url,json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['processed']==1
    assert calls[0][0]['shared_material']=='共享材料'
    assert (await client.post(url,json=payload)).json()['processed']==0
    assert (await client.post(url,json=dict(payload,force=True))).json()['processed']==1
    assert len(calls)==2
    assert await session.scalar(select(func.count()).select_from(QuestionClassificationRun).where(QuestionClassificationRun.import_item_id==uuid.UUID(block['items'][0]['id'])))==2
    refreshed=(await client.get('/api/v1/admin/markdown-batches/'+batch)).json()['blocks'][0]
    assert refreshed['items'][0]['candidates'] and not refreshed['items'][1]['candidates']
    assert not refreshed['confirmed']


async def test_repair_preview_never_mutates_or_changes_published(client, session, storage):
    batch=await upload(client)
    base='/api/v1/admin/markdown-batches/'+batch
    before=(await client.get(base)).json()
    block=before['blocks'][0]
    result=await client.post(base+'/blocks/'+block['id']+'/repair-preview')
    assert result.status_code==200
    assert result.json()['suggestion']['explanation_markdown']=='共同解析'
    assert (await client.get(base)).json()==before
    await review(client,session,batch)
    assert (await client.post(base+'/blocks/'+block['id']+'/repair-preview')).status_code==409


async def test_classification_failure_keeps_draft_and_exposes_reason(client, session, storage, monkeypatch):
    batch=await upload(client)
    base='/api/v1/admin/markdown-batches/'+batch
    block=(await client.get(base)).json()['blocks'][0]
    class BrokenAI:
        provider_name='fake'
        classification_model='fake'
        async def classify_questions(self,*args):
            raise RuntimeError('model unavailable')
    monkeypatch.setattr('app.imports.ai.OpenAICompatibleQuestionClient',lambda settings:BrokenAI())
    response=await client.post(base+'/classify',json={'block_id':block['id']})
    assert response.status_code==200, response.text
    assert response.json()['failed_items']==2
    assert response.json()['results'][0]['error']=='model unavailable'
    current=(await client.get(base)).json()['blocks'][0]
    assert current['parts']==block['parts'] and not current['confirmed']
    assert current['items'][0]['classification_status']=='分类失败'


async def test_automatic_recommendation_never_approves(client, session, storage, monkeypatch):
    from app.config import get_settings
    from app.imports.schemas import AIClassificationBatch
    topic=await session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_type=='topic'))
    calls=[]
    class FakeAI:
        provider_name='fake'
        classification_model='fake'
        async def classify_questions(self,items,catalog):
            calls.extend(i['question_no'] for i in items)
            return AIClassificationBatch.model_validate({'results':[{'question_no':i['question_no'],'primary_candidates':[{'code':topic.code,'confidence':0.8,'rationale':'分类依据'}]} for i in items]}), {}
    monkeypatch.setattr(get_settings(),'ai_api_key','fake')
    monkeypatch.setattr(get_settings(),'ai_classification_model','fake')
    monkeypatch.setattr('app.imports.ai.OpenAICompatibleQuestionClient',lambda settings:FakeAI())
    batch=await upload(client)
    state=(await client.get('/api/v1/admin/markdown-batches/'+batch)).json()
    assert sorted(calls)==[17,18,99]
    assert all(not b['confirmed'] for b in state['blocks'])
    assert all(i['candidates'] for b in state['blocks'] for i in b['items'])


async def test_parallel_classification_is_rejected_without_second_job(client, session, storage):
    from sqlalchemy import text
    batch=await upload(client)
    async with session.bind.connect() as other:
        transaction=await other.begin()
        await other.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'classify:'+batch})
        response=await client.post('/api/v1/admin/markdown-batches/'+batch+'/classify',json={})
        assert response.status_code==409
        await transaction.rollback()


async def test_pending_assist_only_proposes_never_replaces(client, session, storage, monkeypatch):
    from app.imports import markdown_api
    from app.imports.markdown_parser import Unit
    from types import SimpleNamespace
    batch=await upload(client,source='1-2. 材料\nA.a B.b C.c D.d\n答案：A B')
    base='/api/v1/admin/markdown-batches/'+batch
    before=(await client.get(base)).json()
    async def suggestion(*args):
        return Unit.model_validate({'source_label':'1-2','material_markdown':'不同的材料','parts':before['blocks'][0]['parts']})
    monkeypatch.setattr(markdown_api,'assist',suggestion)
    monkeypatch.setattr(markdown_api,'get_settings',lambda:SimpleNamespace(ai_api_key='fake',ai_text_model='fake'))
    assert (await client.post(base+'/assist-pending')).status_code==200
    after=(await client.get(base)).json()
    assert after['blocks'][0]['material_markdown']==before['blocks'][0]['material_markdown']
    assert not after['blocks'][0]['confirmed']


async def seed_legacy_review_candidates(session, batch_id):
    # Freeze the pre-upgrade wire shape independently of production hash helpers.
    import hashlib
    from app.models import QuestionClassificationRun
    items, topic = await seed_review_candidates(session, batch_id)
    for item in items:
        group = await session.get(QuestionImportGroup, item.group_id)
        run = await session.scalar(select(QuestionClassificationRun).where(QuestionClassificationRun.import_item_id == item.id))
        payload = dict(stem=item.stem_markdown, options=item.options_payload,
                       answer=item.correct_option_keys, explanation=item.explanation_markdown,
                       shared_material=group.material_markdown if group else None)
        run.input_fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        run.raw_response = {'results': [{'question_no': item.question_no}]}
    await session.commit()
    return items, topic


async def test_upgrade_reuses_legacy_recommendations_everywhere(client, session, storage):
    from app.imports.service import classify_batch, get_item_read, _load_approval_suggestions
    from app.imports.ai import CLASSIFICATION_PROMPT_VERSION
    from app.models import QuestionClassificationRun, QuestionImportKnowledgeSelection
    batch_id = await upload(client, source=SOURCE.replace('解析：共同解析', '解析：第一问一。第二问二。'))
    items, topic = await seed_legacy_review_candidates(session, batch_id)
    batch = await session.get(QuestionImportBatch, uuid.UUID(batch_id))
    runs = list(await session.scalars(select(QuestionClassificationRun).where(QuestionClassificationRun.import_item_id.in_([i.id for i in items]))))
    hashes = {r.id:r.input_fingerprint for r in runs}
    for run in runs:
        run.prompt_version = CLASSIFICATION_PROMPT_VERSION
    await session.commit()
    state = (await client.get('/api/v1/admin/markdown-batches/'+batch_id)).json()
    assert all(b['approval']['eligible'] for b in state['blocks'])
    assert all(i['classification_status']=='推荐待确认' and i['candidates'] for b in state['blocks'] for i in b['items'])
    assert len(await _load_approval_suggestions(session, batch, items)) == len(items)
    assert (await get_item_read(session, items[0].id)).candidates
    class NoAI:
        async def classify_questions(self, *args):
            raise AssertionError('Unchanged legacy content must not call AI')
    assert (await classify_batch(session,batch.id,ai_client=NoAI(),limit=5)).processed == 0
    result = await client.post('/api/v1/admin/markdown-batches/'+batch_id+'/approve-ready',json={'block_ids':[b['id'] for b in state['blocks']]})
    assert result.status_code == 200, result.text
    assert result.json()['approved'] == 2
    selections=list(await session.scalars(select(QuestionImportKnowledgeSelection).where(QuestionImportKnowledgeSelection.import_item_id.in_([i.id for i in items]))))
    assert all(selection.candidate_id is not None for selection in selections)
    assert {r.id:r.input_fingerprint for r in runs} == hashes


@pytest.mark.parametrize('change', ['stem','options','answer','explanation','material','shared_explanation','number','taxonomy','checksum','null_hash','missing_evidence'])
async def test_upgrade_does_not_reuse_unverified_legacy_classification(client, session, storage, change):
    from app.imports.service import classification_run_matches, _load_approval_suggestions, get_item_read
    from app.models import QuestionClassificationRun, KnowledgeTaxonomyRelease
    batch_id=await upload(client, source=SOURCE.replace('解析：共同解析','解析：第一问一。第二问二。'))
    items, _=await seed_legacy_review_candidates(session,batch_id)
    item=items[0]
    group=await session.get(QuestionImportGroup,item.group_id)
    batch=await session.get(QuestionImportBatch,item.batch_id)
    release=await session.get(KnowledgeTaxonomyRelease,batch.taxonomy_release_id)
    run=await session.scalar(select(QuestionClassificationRun).where(QuestionClassificationRun.import_item_id==item.id))
    if change=='stem': item.stem_markdown+='changed'
    elif change=='options': item.options_payload=[dict(o,content_markdown=o['content_markdown']+'changed') for o in item.options_payload]
    elif change=='answer': item.correct_option_keys=['D']
    elif change=='explanation': item.explanation_markdown='changed'
    elif change=='material': group.material_markdown+='changed'
    elif change=='shared_explanation': group.explanation_markdown='new shared solution'
    elif change=='number': item.question_no=16
    elif change=='taxonomy': batch.taxonomy_release_id=uuid.uuid4()
    elif change=='checksum': run.catalog_checksum='0'*64
    elif change=='null_hash': run.input_fingerprint=None
    elif change=='missing_evidence': run.raw_response=None
    assert not classification_run_matches(run,item,group,batch,release)
    if change != 'taxonomy':
        await session.flush()
        state=(await client.get('/api/v1/admin/markdown-batches/'+batch_id)).json()
        visible=next(i for b in state['blocks'] for i in b['items'] if i['id']==str(item.id))
        assert not visible['candidates']
        assert item.id not in await _load_approval_suggestions(session,batch,items)
        assert not (await get_item_read(session,item.id)).candidates
