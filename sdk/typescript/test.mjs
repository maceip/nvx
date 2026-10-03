import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {test} from 'node:test';
import {Client, PROTOCOL} from './dist/index.js';

test('MCP negotiation and split streaming frames preserve both byte streams', async () => {
  const requests = [];
  const server = createServer(async (request, response) => {
    assert.equal(request.headers.authorization, 'Bearer fixture');
    let raw = '';
    for await (const chunk of request) raw += chunk;
    const message = JSON.parse(raw);
    requests.push(message.method);
    if (message.id === undefined) { response.writeHead(202); response.end(); return; }
    response.writeHead(200, {'Content-Type': 'text/event-stream'});
    const frame = value => `event: message\ndata: ${JSON.stringify(value)}\n\n`;
    let result = {};
    if (message.method === 'initialize') result = {protocolVersion: PROTOCOL};
    else if (message.method === 'tools/list') result = {tools: [{name: 'nvx_run'}]};
    else {
      for (const [stream, bytes] of [['stdout', Buffer.from([0, 255])], ['stderr', Buffer.from('error')]]) {
        const value = frame({jsonrpc:'2.0', method:'notifications/progress', params:{message:JSON.stringify({stream, data:bytes.toString('base64')})}});
        response.write(value.slice(0, 19)); response.write(value.slice(19));
      }
      result = {isError:false, content:[{type:'text',text:JSON.stringify({returncode:37})}]};
    }
    response.end(frame({jsonrpc:'2.0',id:message.id,result}));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const client = new Client(`http://127.0.0.1:${server.address().port}/mcp`, 'fixture');
    assert.equal((await client.initialize()).protocolVersion, PROTOCOL);
    assert.equal((await client.tools())[0].name, 'nvx_run');
    const output = [];
    const result = await client.run('fixture', ['/bin/true'], 'same', {}, {output:(stream, bytes) => output.push([stream, Buffer.from(bytes)]), timeoutMs:3000});
    assert.equal(result.returncode, 37);
    assert.deepEqual(output, [['stdout',Buffer.from([0,255])],['stderr',Buffer.from('error')]]);
    assert.deepEqual(requests, ['initialize','notifications/initialized','tools/list','tools/call']);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('cancellation sends the request ID before aborting its response', async () => {
  let cancelled;
  const server = createServer(async (request, response) => {
    let raw = ''; for await (const chunk of request) raw += chunk;
    const message = JSON.parse(raw);
    if (message.method === 'notifications/cancelled') {
      cancelled = message.params.requestId; response.writeHead(202); response.end();
    } else { response.writeHead(200, {'Content-Type':'text/event-stream'}); response.flushHeaders(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const client = new Client(`http://127.0.0.1:${server.address().port}/mcp`, 'fixture');
    const operation = client.startRun('fixture', ['/bin/true'], 'cancelled');
    const rejected = assert.rejects(operation.result, /abort/i);
    await operation.cancel(); await rejected;
    assert.equal(cancelled, operation.id);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});
