/* Synthetic UI checks: no browser, credentials, stored papers or model calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'static/app.js'), 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const clone = (value) => JSON.parse(JSON.stringify(value));
let writes = 0;
function element(tag = 'div', className = '', text = '') {
  const result = { tag, className, textContent: text, dataset: {}, style: {}, children: [], attributes: {},
    get childNodes() { return this.children; },
    listeners: {},
    hidden: false, disabled: false, checked: false, value: '', open: false,
    classList: { toggle() { writes++; }, add() { writes++; }, remove() { writes++; } },
    append(...items) { writes++; this.children.push(...items); },
    replaceChildren(...items) { writes++; this.children = items; },
    setAttribute(name, value) { writes++; this.attributes[name] = value; },
    removeAttribute(name) { writes++; delete this.attributes[name]; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    querySelector(selector) {
      const matches = (item) => selector.startsWith('.') && item.className?.split(' ').includes(selector.slice(1));
      for (const child of this.children) {
        if (matches(child)) return child;
        const match = child.querySelector?.(selector); if (match) return match;
      }
      return null;
    },
    showModal() { this.open = true; }, close() { this.open = false; },
    reportValidity() { return true; },
  };
  return new Proxy(result, {set(target, key, value) {
    if (['textContent','innerHTML','hidden','title','href','target','disabled','value'].includes(key)) writes++;
    target[key] = value; return true;
  }});
}
const nodes = new Map();
const get = (id) => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
const state = { paper: null, papers: [], settings: {provider:'custom',model:'text-model',has_key:true},
  providers:[{id:'custom',name:'Configured service'}], readMode:'bilingual', originalPage:1,
  bilingual:true, search:'', aiRequestPending:false };
const base = {id:'synthetic-layout', title:'A generic research document', filename:'example.pdf',
  source_format:'pdf', page_count:8, parse_version:7, parse_status:'completed', status:'paused',
  paragraphs:[{id:'before',kind:'paragraph',text:'A complete sentence.',page:4,bbox:[60,200,280,240]}],
  paragraph_count:1, translated_count:0, progress:{total:1,done:0,failed:0}, figures:[],outline:[],
  figure_status:'completed', ai_layout_revision:0 };
let configured = true;
let settingsOpened = 0;
const requests = [];
let handler = async () => ({...state.paper,ai_layout_status:'running',ai_layout_progress:{done:0,total:1,page:4}});
const mathCalls = [];
const sandbox = {state,$:get,node:element,icon:()=>'',document:{querySelectorAll:()=>[]},
  isModelConfigured:()=>configured,openSettings:()=>settingsOpened++,settingsMessage:()=>{},
  captureReparsePosition:()=>({page:4}),toast:()=>{},
  api:async(url,options)=>{requests.push({url,payload:JSON.parse(options.body)});return handler(url,options);},
  displayPaper:(paper)=>{state.paper=paper;},
  paragraphText:(target,text)=>{target.textContent=text;mathCalls.push(text);if(state.search&&text.includes(state.search))target.dataset.mathSearchHit='true';},
};
vm.createContext(sandbox);
vm.runInContext([
  section('function isDemo(', 'function dateLabel('),
  section('function structuredParagraphText(', 'function renderParagraphs('),
  section('function renderTranslationStatus()', 'function renderFigureStatus()'),
  section('function updateAiLayoutDescription()', 'function schedulePoll()'),
  section('async function translatePaper(', 'async function deletePaper('),
].join('\n'),sandbox);

async function main() {
  state.paper=clone(base);
  const originalSettings=clone(state.settings);
  sandbox.openAiLayout();
  assert.equal(requests.length,0,'opening the options must not call any API');
  assert.equal(get('ai-layout-dialog').open,true);
  assert.equal(get('ai-mode-text').checked,true);
  assert.equal(get('ai-mode-vision').checked,false);
  assert.equal(get('ai-layout-page').value,'4');
  assert.equal(get('ai-layout-translate').checked,true);
  assert.match(get('ai-layout-privacy').textContent,/第 4 页的提取文字/);
  assert.match(get('ai-layout-privacy').textContent,/计费/);
  await sandbox.startAiLayout({preventDefault(){}});
  assert.deepEqual(requests[0],{url:'/papers/synthetic-layout/analyze',payload:{scope:'page',page:4,vision:false,translate_after:true}});
  assert.equal(get('ai-layout-dialog').open,false);
  assert.equal(get('ai-layout-banner').hidden,false);
  assert.equal(get('translate-button').disabled,true);
  assert.equal(get('ai-layout-button').disabled,true);
  assert.equal(get('ai-layout-pause').hidden,false);
  await sandbox.translatePaper();
  await sandbox.retryParagraph('before',element('button'));
  assert.equal(requests.length,1,'translation calls cannot race an active restructure');

  handler=async()=>({...state.paper,ai_layout_status:'paused',ai_layout_progress:{done:1,total:8,page:2}});
  await sandbox.pauseAiLayout();
  assert.equal(requests[1].url,'/papers/synthetic-layout/analyze/pause');
  assert.equal(get('ai-layout-pause').hidden,true);
  assert.match(get('ai-layout-detail').textContent,/已完成部分已保存/);
  assert.equal(get('translate-button').disabled,false);

  state.paper=clone(base);
  state.readMode='pdf';state.originalPage=7;
  sandbox.openAiLayout();
  assert.equal(get('ai-layout-page').value,'7','original-PDF mode uses its visible page');
  get('ai-mode-text').checked=false;get('ai-mode-vision').checked=true;
  get('ai-layout-scope').value='all';get('ai-layout-model').value='  image-model  ';
  get('ai-layout-translate').checked=false;
  sandbox.updateAiLayoutDescription();
  assert.equal(get('ai-layout-page').disabled,true);
  assert.match(get('ai-layout-privacy').textContent,/整篇论文（8 页）的页面图像和文字/);
  assert.match(get('ai-layout-privacy').textContent,/支持图片输入/);
  handler=async()=>({...state.paper,ai_layout_status:'running'});
  await sandbox.startAiLayout({preventDefault(){}});
  assert.deepEqual(requests[2].payload,{scope:'all',page:7,vision:true,translate_after:false,model:'image-model'});
  assert.deepEqual(state.settings,originalSettings,'per-operation override does not alter saved settings');
  assert.equal('api_key' in requests[2].payload,false);

  state.paper=clone(base);sandbox.openAiLayout();
  const start=requests.length;
  handler=async()=>{throw new Error('<img src=x onerror=bad> Unsupported image input');};
  await sandbox.startAiLayout({preventDefault(){}});
  assert.equal(requests.length,start+1,'a rejected operation must not silently retry or switch modes');
  assert.equal(get('ai-layout-dialog').open,true);
  assert.match(get('ai-layout-message').textContent,/Unsupported image input/);
  assert.equal(get('ai-layout-message').innerHTML,undefined,'provider failure text remains inert text');
  assert.equal(get('ai-layout-submit').disabled,false);
  assert.equal(state.aiRequestPending,false);

  get('ai-layout-dialog').close();configured=false;
  sandbox.openAiLayout();
  assert.equal(settingsOpened,1);assert.equal(get('ai-layout-dialog').open,false);
  assert.equal(requests.length,start+1);
  configured=true;state.paper={...clone(base),source_format:'txt',filename:'plain.txt'};
  sandbox.openAiLayout();assert.equal(get('ai-layout-dialog').open,false);
  sandbox.renderTranslationStatus();assert.equal(get('ai-layout-button').hidden,true);
  state.paper={...clone(base),is_demo:true};sandbox.openAiLayout();
  assert.equal(get('ai-layout-dialog').open,false);

  // Preserve algorithm structure, safe text, search and the Chinese counterpart.
  state.search='x_';
  const original=element();
  const algorithm={kind:'algorithm',algorithm_lines:[{text:'Input x',indent:0},
    {text:'if \\(x_t>0\\)',indent:1},{text:'<script>untrusted source</script>',indent:2}],ai_reconstructed:true};
  assert.equal(sandbox.structuredParagraphText(original,algorithm),true);
  const listing=original.children[1];
  assert.equal(listing.children.length,3);
  assert.equal(listing.children[1].style.paddingInlineStart,'1.4em');
  assert.equal(listing.children[2].textContent,'<script>untrusted source</script>');
  assert.equal(listing.children[2].innerHTML,undefined);
  assert.equal(original.dataset.mathSearchHit,'true');
  const beforeWrites=writes;
  sandbox.structuredParagraphText(original,algorithm);
  assert.equal(writes,beforeWrites,'identical structure does not rebuild its DOM or repeat math rendering');
  const chinese=element();
  sandbox.structuredParagraphText(chinese,{...algorithm,algorithm_lines:[{text:'输入 x',indent:0},{text:'返回 \\(x_t\\)',indent:1}]},true);
  assert.equal(chinese.children[0].textContent,'算法 · 中文');
  assert.equal(chinese.children[1].children[1].style.paddingInlineStart,'1.4em');
  const table=element();
  sandbox.structuredParagraphText(table,{kind:'table',table_header_rows:1,table_rows:[['Method','Value'],['<b>raw label</b>','\\(a/b\\)']]});
  const viewport=table.children[1];const rows=viewport.children[0].children[0].children;
  assert.equal(viewport.tabIndex,0,'wide tables remain keyboard-scrollable');
  assert.equal(rows[0].children[0].tag,'th');
  assert.equal(rows[0].children[0].attributes.scope,'col');
  assert.equal(rows[1].children[0].textContent,'<b>raw label</b>');
  assert.ok(mathCalls.includes('\\(a/b\\)'), 'table cell formulas go through the existing math renderer');
  assert.equal(sandbox.structuredParagraphText(element(),{kind:'paragraph',text:'Ordinary prose.'}),false);

  // Protected source prose stays complete after merging around a floating algorithm.
  // Recovered math uses the same math renderer; only an unknown formula is cropped.
  const inlineState = {...state,paper:clone(base),blocks:new Map(),readingBlocks:[],search:'completes'};
  const inlineSandbox = {...sandbox,state:inlineState,
    document:{createElement:tag=>element(tag),createTextNode:text=>element('#text','',text),querySelectorAll:()=>[]},
    translatedText:paragraph=>paragraph.translation || '',getFigureTextIds:()=>new Set(),
    cacheReadingTargets:()=>{inlineState.readingBlocks=[...inlineState.blocks.values()];},
    updateSearchHits(){},pruneMathObservations(){},renderEquationSource(){assert.fail('Complete prose must not become one screenshot');},
    paragraphText:(target,text)=>{target.textContent=text;mathCalls.push(text);if(text.includes(inlineState.search))target.dataset.mathSearchHit='true';},
  };
  vm.createContext(inlineSandbox);
  vm.runInContext(section('function translatedParagraphText(', 'function renderEquationSource('),inlineSandbox);
  const completeSource='The update uses ⟦FOLIO_MATH_1⟧ with ⟦FOLIO_MATH_2⟧ after the floating algorithm and completes the sentence.';
  const inlineParagraph={id:'source-inline',kind:'paragraph',page:1,math_status:'source-inline',
    text:'Broken extracted text frag-',translation_text:completeSource,translation:null,
    equation_source:{page:1,bbox:[60,80,500,400]},equation_image:'must-not-render-whole-paragraph.png',
    inline_math:[{id:'known',placeholder:'⟦FOLIO_MATH_1⟧',latex:'x_{t+1}=x_t+1',source:[]},
      {id:'unknown',placeholder:'⟦FOLIO_MATH_2⟧',source:[{page:1,bbox:[110,120,154,133]}]}]};
  inlineState.paper.paragraphs=[inlineParagraph];
  inlineSandbox.renderParagraphs();
  const sourceBlock=inlineState.blocks.get('source-inline');
  const sourceNode=sourceBlock.querySelector('.paragraph-original');
  const descendants=item=>[item,...item.children.flatMap(descendants)];
  const sourceText=()=>descendants(sourceNode).map(item=>item.textContent || '').join('');
  assert.match(sourceText(),/The update uses/);
  assert.match(sourceText(),/after the floating algorithm and completes the sentence\./);
  assert.doesNotMatch(sourceText(),/FOLIO_MATH|Broken extracted/);
  assert.ok(mathCalls.includes('\\(x_{t+1}=x_t+1\\)'),'recovered original math goes through the existing renderer');
  const images=descendants(sourceNode).filter(item=>item.tag==='img');
  assert.equal(images.length,1,'only the unknown formula receives a source crop');
  assert.match(images[0].src,/\/inline-math\/source-inline\/unknown\/0\.png/);
  assert.equal(images[0].width,44);assert.equal(images[0].height,13);
  assert.equal(sourceBlock.querySelector('.paragraph-translation').hidden,true,'untranslated source remains fully readable');
  assert.equal(sourceNode.dataset.mathSearchHit,'true');
  writes=0;inlineSandbox.renderParagraphs();
  assert.equal(writes,0,'unchanged source-inline rendering does not rebuild formulas while polling');
  inlineParagraph.translation_text=completeSource.replace('completes the sentence','completes the repaired sentence');
  inlineSandbox.renderParagraphs();
  assert.match(sourceText(),/completes the repaired sentence/,'the protected source text is included in the paragraph render signature');
  const activeImage=descendants(sourceNode).find(item=>item.tag==='img');
  activeImage.listeners.error();
  assert.equal(activeImage.hidden,true);
  assert.match(sourceText(),/查看原式/,'a failed crop keeps a usable source link');
  for (const formulas of [[],[{id:'empty',placeholder:'⟦FOLIO_MATH_1⟧',source:[]}],
    [{id:'other',placeholder:'⟦FOLIO_MATH_2⟧',latex:'y',source:[]}]]) {
    const missing=element();
    inlineSandbox.translatedParagraphText(missing,'Before ⟦FOLIO_MATH_1⟧ after.',{inline_math:formulas});
    const visible=descendants(missing).map(item=>item.textContent || '').join('');
    assert.doesNotMatch(visible,/FOLIO_MATH/);
    assert.match(visible,/原式.*缺失/,'missing formula metadata is visible instead of silently dropping source mathematics');
    assert.match(visible,/Before.*after\./);
  }

  // A model revision changes IDs without changing the local parser version.
  const lifecycleState={...state,paper:clone(base),readMode:'bilingual',search:'',
    blocks:new Map(),figures:new Map(),readingBlocks:[],outlineItems:[],outlineTargets:[],activeOutline:null};
  const scroll=get('reader-scroll');scroll.scrollTop=1000;scroll.getBoundingClientRect=()=>({top:100});
  const scrollCalls=[];scroll.scrollTo=({top,behavior})=>{scrollCalls.push({top,behavior});scroll.scrollTop=top;};
  const makeBlock=(id,y)=>{const b=element();b.dataset={paragraphId:id,page:'4'};b.getBoundingClientRect=()=>({top:100+y-scroll.scrollTop});return b;};
  lifecycleState.blocks.set('before',makeBlock('before',1020));
  lifecycleState.readingBlocks=[...lifecycleState.blocks.values()];
  let rebuilds=0;
  const lifecycle={...sandbox,state:lifecycleState,dateLabel:()=>'',localStorage:{setItem(){}},
    showView(){},switchSidebarTab(){},updateSummary(){},schedulePoll(){},renderTranslationStatus(){},renderFigureStatus(){},
    clearMathObservations(){},requestAnimationFrame:fn=>fn(),updateReadingPosition(){},
    lastPassedElement:items=>items[0],setReadingMode(){},
    renderParagraphs(){rebuilds++;for(const p of lifecycleState.paper.paragraphs)lifecycleState.blocks.set(p.id,makeBlock(p.id,2000));lifecycleState.readingBlocks=[...lifecycleState.blocks.values()];},
    renderFigures(){},renderOutline(){},
  };
  vm.createContext(lifecycle);
  vm.runInContext(section('function captureReparsePosition(', 'function translatedText('),lifecycle);
  lifecycle.displayPaper(clone(base));rebuilds=0;scrollCalls.length=0;
  const newPaper={...clone(base),ai_layout_revision:1,paragraphs:[{...base.paragraphs[0],id:'merged',bbox:[60,202,280,242]}]};
  lifecycle.displayPaper(newPaper);
  assert.equal(rebuilds,1);
  assert.deepEqual([...lifecycleState.blocks.keys()],['merged']);
  assert.equal(scrollCalls.length,1,'AI regrouping restores the prior page and viewport position');
  assert.equal(scrollCalls[0].behavior,'instant');
  writes=0;rebuilds=0;
  lifecycle.displayPaper(clone(newPaper));
  assert.equal(writes,0);assert.equal(rebuilds,0,'unchanged AI polls do not rebuild the article');

  // Only active AI work keeps polling once ordinary translation/artwork stops.
  const timers=[];
  sandbox.clearTimeout=()=>{};sandbox.setTimeout=(callback,delay)=>{timers.push({callback,delay});return timers.length;};
  vm.runInContext(section('function schedulePoll()', 'async function translatePaper('),sandbox);
  state.paper={...clone(base),ai_layout_status:'running'};sandbox.schedulePoll();assert.equal(timers.length,1);
  state.paper.ai_layout_status='error';sandbox.schedulePoll();assert.equal(timers.length,1);
  console.log('PASS: AI layout stays opt-in, preserves reading position, safely renders bilingual structures, and keeps complete source-inline prose/math readable without repeated DOM work.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
