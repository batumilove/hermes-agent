import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { EventEmitter } from 'node:events'
const source = readFileSync(process.env.HARNESS_PATH || '/harness.ts', 'utf8')
const body = source.slice(source.indexOf('export function recordWebSockets('), source.indexOf('/** Chromium can omit close events'))
const record = new Function(stripTypeScriptTypes(body.replace('export function', 'function')) + '; return recordWebSockets')()
function setup() {
 const page = new EventEmitter(), socket = new EventEmitter()
 socket.url = () => 'ws://fixture/api/ws?token=PRIVATE'
 const rec = record(page)
 page.emit('websocket', socket)
 const emit = (direction, msg) => socket.emit(direction, { payload: JSON.stringify(msg) })
 return { page, socket, rec, emit }
}
test('redacted recorder observes answers without adding requests', () => {
 const {page,socket,rec,emit} = setup()
 emit('framesent', {id:'PRIVATE-request',method:'session.activate',params:{session_id:'PRIVATE-session',token:'PRIVATE'}})
 emit('framereceived', {id:'PRIVATE-request',result:{running:false,messages:[{content:'PRIVATE'}],messages_omitted:true}})
 socket.emit('close'); page.emit('pageerror',new Error('PRIVATE'))
 assert.deepEqual(rec.diagnostics.map(x=>x.kind),['socket-created','rpc-request','rpc-answer','socket-closed','page-error'])
 assert.equal(rec.diagnostics[2].failed,false)
 assert.equal(rec.diagnostics[1].id,rec.diagnostics[2].id)
 assert.equal(JSON.stringify(rec.diagnostics).includes('PRIVATE'),false)
 assert.equal(rec.sent.length,1)
})
test('adversarial nested and string secret fields are rejected by scalar schema', () => {
 for (const secret of ['PRIVATE', { nested:'PRIVATE' }, ['PRIVATE'], null, 1e100]) {
  const {rec,emit} = setup()
  emit('framesent',{id:'PRIVATE',method:'session.events.since',params:{session_id:secret,last_seen:secret,replay_epoch:secret}})
  emit('framereceived',{id:'PRIVATE',result:{running:secret,messages_omitted:secret,replay_epoch:secret,latest_seq:secret,snapshot_required:secret,truncated:secret},error:{code:secret}})
  emit('framereceived',{method:'event',params:{type:secret,session_id:secret,seq:secret,replay_epoch:secret}})
  assert.equal(JSON.stringify(rec.diagnostics).includes('PRIVATE'),false)
  for (const row of rec.diagnostics) for (const [key,value] of Object.entries(row)) {
   assert.ok(['at','kind','socket','id','method','session','lastSeen','replayEpoch','failed','code','running','messagesOmitted','latestSeq','snapshotRequired','truncated','eventCount','type','seq'].includes(key))
   assert.ok(['string','number','boolean'].includes(typeof value),key)
   if (typeof value === 'number') assert.ok(Number.isFinite(value) && Math.abs(value)<=Number.MAX_SAFE_INTEGER)
  }
 }
})
test('diagnostics and correlation state remain bounded', () => {
 const {rec,emit} = setup()
 for(let i=0;i<6000;i++) emit('framesent',{id:i,method:'session.resume',params:{session_id:`PRIVATE-${i}`}})
 assert.equal(rec.diagnostics.length,5000)
 assert.equal(JSON.stringify(rec.diagnostics).includes('PRIVATE'),false)
})

// Generated registry plus authority overflow emitter are the wire-name authorities.
const contract = readFileSync(process.env.CONTRACT_PATH || new URL('./gateway-contract.generated.ts', import.meta.url), 'utf8')
const names = [...contract.split('export const GATEWAY_EVENT_TYPES = [')[1].split('] as const')[0].matchAll(/'([^']+)'/g)].map(x => x[1])
const authority = readFileSync(process.env.SESSION_EVENTS_PATH || new URL('../../../gateway/session_events.py', import.meta.url), 'utf8')
assert.ok(authority.includes("'type': 'session.replay_gap'"))
for (const type of [...names, 'session.replay_gap']) test(`current protocol discriminator: ${type}`, () => {
 const {rec,emit} = setup()
 emit('framereceived',{method:'event',params:{type,session_id:'PRIVATE',seq:7,replay_epoch:'PRIVATE-epoch',payload:{text:'PRIVATE'}}})
 const row = rec.diagnostics.find(x => x.kind === 'event')
 assert.equal(row.type,type)
 assert.equal(row.seq,7)
 assert.match(row.session,/^c\d+$/)
 assert.equal(JSON.stringify(rec.diagnostics).includes('PRIVATE'),false)
})
test('obsolete and unknown names never become metadata discriminators', () => {
 for (const type of ['stream_start','stream_chunk','stream_end','complete','session_updated','PRIVATE']) {
  const {rec,emit}=setup()
  emit('framereceived',{method:'event',params:{type,session_id:'PRIVATE'}})
  assert.equal(rec.diagnostics.find(x=>x.kind==='event').type,undefined)
 }
})
