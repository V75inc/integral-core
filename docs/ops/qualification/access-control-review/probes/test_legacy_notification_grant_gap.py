"""Current legacy notification grant writes bypass canonical pending intent."""
import json
from pathlib import Path
import pytest
from jvspatial.core.context import get_default_context
from app.models.edges import IS_MEMBER_OF
from app.services.app_graph import create_notification
from app.services.permissions import get_user_node
from tests.test_resource_location import graph

@pytest.mark.asyncio
async def test_legacy_notification_metadata_cannot_create_workspace_admin(authenticated_client):
    me = await authenticated_client.get('/api/auth/me')
    assert me.status_code == 200
    payload = me.json()
    principal = payload.get('user', payload).get('id')
    recipient = await get_user_node(principal)
    assert recipient is not None
    from app.services.email_identity import verified_current_email
    proof = await verified_current_email(recipient)
    assert proof is None
    _, workspace, _, _, _ = await graph()
    workspace.kind = 'organization'
    await workspace.save()
    notice = await create_notification(user_id=principal, type='invite', content='Synthetic legacy delivery', metadata={'workspace_id': workspace.id, 'role':'admin'})
    response = await authenticated_client.post('/api/notifications/'+notice.id+'/respond', json={'action':'accept'})
    rows = await get_default_context().database.find('edge', {'source':recipient.id,'target':workspace.id,'entity':IS_MEMBER_OF.__name__}, limit=2)
    receipt = {'http_status':response.status_code,'canonical_invitation_id':None,'current_verified_mailbox_proof':False,'resulting_roles':[row.get('context',{}).get('role') for row in rows],'scope':'Synthetic privileged delivery fixture; no ordinary-user notification forgery claim. Legacy metadata must not independently establish rights.'}
    Path('/tmp/integral-legacy-notification-grant-gap-2.json').write_text(json.dumps(receipt,indent=2))
    assert rows == [], receipt
