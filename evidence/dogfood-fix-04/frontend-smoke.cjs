const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const html = fs.readFileSync('web_console/index.html', 'utf8').replace(/\r\n/g, '\n');
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
function fn(name) {
  const start = html.indexOf('function ' + name + '(');
  const end = html.indexOf('\n}\n', start) + 3;
  assert(start >= 0 && end > start);
  return html.slice(start, end);
}
let facts;
const sandbox = {document: {getElementById: () => ({})}, fillDetails: (_, rows) => {facts = rows;}};
vm.createContext(sandbox);
vm.runInContext(['usageText', 'tokensLabel', 'renderSupervisorUsage'].map(fn).join('\n'), sandbox);
assert.equal(sandbox.usageText({reported:false,input_tokens:0}), 'usage not reported');
const label = sandbox.usageText({reported:true,input_tokens:12,cached_input_tokens:4,output_tokens:3,reasoning_output_tokens:0});
assert(label.includes('cached input 4') && label.includes('reasoning output 0') && label.includes('provider reported'));
assert(!label.includes('total'));
sandbox.renderSupervisorUsage({sum_label:'Partial reported sums',turns_with_reported_usage:1,turns_total:3,totals:{input_tokens:12,cached_input_tokens:4,output_tokens:3,reasoning_output_tokens:0,total_tokens:null},coverage:{input_tokens_reported:1,cached_input_tokens_reported:1,output_tokens_reported:1,reasoning_output_tokens_reported:1,total_tokens_reported:0},zcode_usage:{rounds_total:5}});
assert(facts.some(([key,value]) => key === 'Usage scope' && value === 'Partial reported sums'));
assert(facts.some(([key,value]) => key === 'Input tokens (reported)' && value.includes('1 of 3 turns reported')));
assert(!facts.some(([key]) => key === 'Total tokens (reported)'));
assert(facts.some(([key,value]) => key === 'Executor reported rounds' && value === '0 of 5 authorized rounds'));
sandbox.renderSupervisorUsage({sum_label:'Partial reported sums',turns_with_reported_usage:0,turns_total:7,totals:{input_tokens:null,output_tokens:null,total_tokens:null},coverage:{},zcode_usage:{rounds_total:5}});
assert(facts.some(([key,value]) => key === 'Usage' && value === 'Not reported'));
assert(!facts.some(([key]) => key.includes('tokens (reported)')));
console.log(JSON.stringify({result:'PASS',checks:11,scope:'actual frontend JavaScript syntax and rendering functions; synthetic test data only'},null,2));
