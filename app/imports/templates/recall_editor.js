// Continuous text editors share the existing token/blank schema with the table.
let selectedBlank = null;
let textSelection = null;
const fieldWidths = new WeakMap();
const fieldResizeObserver = new ResizeObserver(entries => {
  for (const entry of entries) {
    const width = entry.contentRect.width;
    if (width > 0 && width !== fieldWidths.get(entry.target)) {
      fieldWidths.set(entry.target, width);
      requestAnimationFrame(() => {
        if (entry.target.isConnected) resizeBlankField(entry.target);
      });
    }
  }
});

function validateTextLengths(value) {
  if (Array.isArray(value)) value.forEach(validateTextLengths);
  else if (value && typeof value === 'object') {
    if (typeof value.text === 'string' && value.text.length > 2000)
      throw new Error('正文文字片段不能超过 2000 字，请拆分段落后保存。');
    Object.values(value).forEach(validateTextLengths);
  }
}

function captureTextSelection() {
  const selection = getSelection();
  textSelection = null;
  if (!selection?.rangeCount || selection.isCollapsed) return;
  const range = selection.getRangeAt(0);
  const start = range.startContainer.nodeType === Node.ELEMENT_NODE
    ? range.startContainer : range.startContainer.parentElement;
  const node = start?.closest('.cell-text-segment');
  if (!node || !node.contains(range.endContainer) || !node.isContentEditable) return;
  const before = range.cloneRange();
  before.selectNodeContents(node);
  before.setEnd(range.startContainer, range.startOffset);
  textSelection = { node, start: before.toString().length,
    end: before.toString().length + range.toString().length, source: node.textContent };
}
document.addEventListener('selectionchange', captureTextSelection);

function blankCaption(key) {
  const index = model.blanks.findIndex(b => b.key === key);
  const blank = model.blanks[index];
  return `${index + 1}. ${blank?.answer || blank?.label || '待填写答案'}`;
}
function contextForBlank(key) {
  const c = model.content;
  if (c.type === 'table') {
    for (let r = 0; r < c.rows.length; r++) for (let col = 0; col < c.columns.length; col++) {
      if (keysIn(c.rows[r][col]).has(key))
        return `第 ${r + 1} 行 · ${c.columns[col]}\n${tableCellText(c.rows[r][col])}`;
    }
  } else if (c.type === 'sentence') {
    const i = c.paragraphs.findIndex(p => keysIn(p).has(key));
    if (i >= 0) return `第 ${i + 1} 段\n${tableCellText(c.paragraphs[i])}`;
  }
  return '请在正文中关联此空位';
}
function syncBlankLabels() {
  document.querySelectorAll('[data-blank-chip]').forEach(node => {
    node.textContent = blankCaption(node.dataset.blankChip);
    node.classList.toggle('blank-active', node.dataset.blankChip === selectedBlank);
  });
  document.querySelectorAll('[data-blank-summary]').forEach(node => {
    node.textContent = blankCaption(node.dataset.blankSummary);
  });
  document.querySelectorAll('[data-blank-context]').forEach(node => {
    node.textContent = contextForBlank(node.dataset.blankContext);
  });
}
function focusBlank(key) {
  selectedBlank = key;
  const panels = document.querySelectorAll('.blank-editor');
  panels.forEach(node => {
    const active = node.dataset.blankKey === key;
    node.open = active;
    node.classList.toggle('blank-focus', active);
  });
  syncBlankLabels();
  const node = [...panels].find(n => n.dataset.blankKey === key);
  if (!node) return;
  node.querySelectorAll('textarea').forEach(resizeBlankField);
  node.scrollIntoView({ block: 'nearest' });
  node.querySelector('[data-answer]')?.focus({ preventScroll: true });
}
function resizeBlankField(node) {
  node.style.height = 'auto';
  node.style.height = Math.max(76, node.scrollHeight) + 'px';
}
function longField(parent, label, value, change, maxLength = 2000) {
  const wrapper = el('label', label), input = el('textarea');
  input.value = value || '';
  input.rows = 2;
  input.maxLength = maxLength;
  input.readOnly = !can('edit');
  const update = () => {
    change(input.value);
    resizeBlankField(input);
    syncBlankLabels();
    markChanged();
  };
  input.oninput = event => { if (!event.isComposing) update(); };
  input.addEventListener('compositionend', update);
  wrapper.append(input);
  parent.append(wrapper);
  fieldResizeObserver.observe(input);
  requestAnimationFrame(() => resizeBlankField(input));
  return input;
}
function moveBlank(key, offset) {
  const from = model.blanks.findIndex(b => b.key === key), to = from + offset;
  if (!can('edit') || from < 0 || to < 0 || to >= model.blanks.length) return;
  const [blank] = model.blanks.splice(from, 1);
  model.blanks.splice(to, 0, blank);
  selectedBlank = key;
  renderBlanks();
  syncBlankLabels();
  markChanged();
}
function renderBlanks() {
  const box = $('blanks');
  fieldResizeObserver.disconnect();
  box.replaceChildren();
  if (!blankByKey(selectedBlank)) selectedBlank = model.blanks[0]?.key || null;
  if (!model.blanks.length) box.append(el('p', '在正文中选中文字并设为空位，即可在这里编辑答案和提示。'));
  model.blanks.forEach((b, i) => {
    const wrap = el('details');
    wrap.className = 'blank-editor';
    wrap.dataset.blankKey = b.key;
    wrap.open = b.key === selectedBlank;
    wrap.classList.toggle('blank-incomplete', blankIncomplete(b));
    const summary = el('summary', blankCaption(b.key));
    summary.dataset.blankSummary = b.key;
    wrap.append(summary);
    wrap.ontoggle = () => {
      if (!wrap.open) return;
      selectedBlank = b.key;
      box.querySelectorAll('.blank-editor').forEach(other => { if (other !== wrap) other.open = false; });
      wrap.querySelectorAll('textarea').forEach(resizeBlankField);
      syncBlankLabels();
    };
    const context = el('p', contextForBlank(b.key));
    context.className = 'blank-context';
    context.dataset.blankContext = b.key;
    wrap.append(context);
    const status = el('p');
    status.className = 'blank-status';
    const statusUpdate = () => {
      const incomplete = blankIncomplete(b);
      wrap.classList.toggle('blank-incomplete', incomplete);
      status.textContent = incomplete ? '待补全：空位说明、标准答案及两级提示均为必填。' : '';
    };
    statusUpdate();
    wrap.append(status);
    longField(wrap, '空位说明', b.label, v => b.label = v, 200);
    longField(wrap, '标准答案', b.answer, v => b.answer = v).dataset.answer = 'true';
    longField(wrap, '其他可接受答案（每行一个）', b.acceptable_answers.join('\n'),
      v => b.acceptable_answers = v.split('\n').map(x => x.trim()).filter(Boolean), 60030);
    longField(wrap, '第一次错误：轻提示', b.hints[0], v => b.hints[0] = v);
    longField(wrap, '第二次错误：关键线索', b.hints[1], v => b.hints[1] = v);
    const advanced = el('details');
    advanced.className = 'advanced-rules';
    advanced.append(el('summary', '高级判分设置'));
    const key = field(advanced, '空位标识', b.key, () => {});
    key.readOnly = true;
    b.normalization ??= {case_sensitive:true,whitespace:'preserve',aliases:{},unordered_set:false,separator:','};
    const rule = el('div');
    rule.className = 'editor-row';
    select(rule,'字母大小写',String(b.normalization.case_sensitive),[['true','区分'],['false','不区分']],v=>b.normalization.case_sensitive=v==='true');
    select(rule,'内部空白',b.normalization.whitespace,[['preserve','保留'],['collapse','合并'],['remove','忽略']],v=>b.normalization.whitespace=v);
    select(rule,'无序集合',String(b.normalization.unordered_set),[['false','否'],['true','是']],v=>b.normalization.unordered_set=v==='true');
    field(rule,'集合分隔符',b.normalization.separator,v=>b.normalization.separator=v);
    advanced.append(rule);
    longField(advanced,'符号别名（每项 原符号=替换值，用 | 分隔）',Object.entries(b.normalization.aliases).map(([a,z])=>a+'='+z).join(' | '),v=>{
      b.normalization.aliases=Object.fromEntries(v.split('|').filter(x=>x.includes('=')).map(x=>{const p=x.indexOf('=');return[x.slice(0,p).trim(),x.slice(p+1).trim()]}));
    });
    wrap.append(advanced);
    advanced.addEventListener('change', markChanged);
    if (can('edit')) {
      const order = el('div'); order.className = 'blank-order';
      const up = button('提前一空', () => moveBlank(b.key, -1)); up.disabled = i === 0;
      const down = button('延后一空', () => moveBlank(b.key, 1)); down.disabled = i === model.blanks.length - 1;
      order.append(up, down, button('取消挖空', () => removeBlankAt(model.blanks.indexOf(b))));
      wrap.append(order);
    } else advanced.querySelectorAll('input,select,textarea').forEach(n => n.disabled = true);
    wrap.addEventListener('input', statusUpdate);
    wrap.addEventListener('compositionend', statusUpdate);
    box.append(wrap);
  });
  syncBlankLabels();
}

function renderTokenEditor(parent, tokens, setTokens, label) {
  // Adjacent plain tokens are one selectable run; blank boundaries remain atomic.
  tokens = mergeTextTokens(tokens.map(token => ({...token})));
  if (tokens[0]?.blank_key) tokens.unshift({text:''});
  if (tokens.at(-1)?.blank_key) tokens.push({text:''});
  for (let i = tokens.length - 1; i > 0; i--) {
    if (tokens[i].blank_key && tokens[i-1].blank_key) tokens.splice(i, 0, {text:''});
  }
  const segments = el('div'); segments.className = 'cell-segments';
  const error = el('p'); error.className = 'editor-error'; error.setAttribute('role','status');
  let composing = false;
  tokens.forEach((item, index) => {
    if (item.blank_key) {
      const chip = button(blankCaption(item.blank_key), () => focusBlank(item.blank_key));
      chip.className = 'blank-token'; chip.dataset.blankChip = item.blank_key;
      chip.title = '编辑这个空位的答案、提示和判分规则';
      segments.append(chip);
    } else {
      const text = el('span', item.text || '');
      text.className = 'cell-text-segment';
      text.contentEditable = can('edit') ? 'true' : 'false';
      text.spellcheck = false;
      text.dataset.placeholder = tokens.length === 1 ? '输入完整内容' : '文字';
      text.setAttribute('aria-label', label);
      const update = () => {
        item.text = text.innerText.replace(/\r/g, '');
        setTokens(tokens);
        error.textContent = item.text.length > 2000 ? '此文字片段超过 2000 字，请拆分段落后保存。' : '';
        syncBlankLabels(); markChanged();
      };
      text.addEventListener('compositionstart', () => { composing = true; });
      text.addEventListener('compositionend', () => { composing = false; update(); captureTextSelection(); });
      text.oninput = event => { if (!composing && !event.isComposing) update(); };
      text.onmouseup = text.onkeyup = captureTextSelection;
      text.onpaste = insertPlainText;
      text.ondrop = event => event.preventDefault();
      text.dataset.segment = String(index);
      segments.append(text);
    }
  });
  parent.append(segments);
  if (can('edit')) {
    const actions = el('div'); actions.className = 'cell-actions';
    const selected = button('挖空所选文字', () => {
      try {
        const selection = textSelection;
        if (composing || !selection || !segments.contains(selection.node) || !selection.node.isConnected || selection.source !== selection.node.textContent)
          throw new Error('请在同一段落或单元格内选择连续文字，不能跨越已有空位。');
        const index = Number(selection.node.dataset.segment), token = tokens[index];
        let {start, end} = selection;
        if (!token || token.text === undefined) throw new Error('只能选择普通文字挖空。');
        while (start < end && /\s/.test(token.text[start])) start++;
        while (end > start && /\s/.test(token.text[end - 1])) end--;
        const answer = token.text.slice(start, end);
        if (!answer.trim()) throw new Error('不能将空白字符设为空位。');
        if (answer.length > 2000) throw new Error('单个空位答案不能超过 2000 字。');
        if (model.content.type === 'table' && tokens.length + 2 > 50) throw new Error('此单元格片段过多，请先简化内容。');
        const blank = makeBlank(answer);
        tokens.splice(index,1,{text:token.text.slice(0,start)},{blank_key:blank.key},{text:token.text.slice(end)});
        setTokens(tokens);
        selectedBlank = blank.key; textSelection = null;
        refreshTable(); focusBlank(blank.key);
      } catch (e) { error.textContent = e.message; }
    });
    selected.onmousedown = event => event.preventDefault();
    actions.append(selected); parent.append(actions);
  }
  parent.append(error);
}
function renderTableCell(td, row, column) {
  const editor = el('div'); editor.className = 'cell-editor'; editor.dataset.cell = `${row}-${column}`;
  const c = model.content;
  renderTokenEditor(editor,cellTokens(c.rows[row][column]),tokens=>c.rows[row][column]=tokens,
    `第 ${row+1} 行 ${column+1} 列：${c.columns[column] || '未命名列'}`);
  if (can('edit')) editor.querySelector('.cell-actions').append(button('整格挖空', () => {
    try { blankWholeCell(row,column); } catch(e) { editor.querySelector('.editor-error').textContent=e.message; }
  }));
  td.append(editor);
}
function renderSentenceParagraphs(box, paragraphs) {
  paragraphs.forEach((p,i) => {
    const row = el('section'); row.className = 'paragraph-editor'; row.dataset.paragraph = String(i);
    const heading = el('div'); heading.className = 'paragraph-heading'; heading.append(el('strong', `第 ${i+1} 段`));
    if (can('edit')) {
      const remove = button('删除段落', () => {
        const keys = keysIn(paragraphs[i]);
        if (keys.size && !confirm(`本段包含 ${keys.size} 个空位，删除段落会同时删除对应配置，是否继续？`)) return;
        removeBlankDefinitions(keys); paragraphs.splice(i,1); refreshTable();
      });
      remove.disabled = paragraphs.length <= 1; heading.append(remove);
    }
    row.append(heading);
    renderTokenEditor(row,p,tokens=>paragraphs[i]=tokens,`第 ${i+1} 段正文`);
    box.append(row);
  });
  if (can('edit')) {
    const add = button('添加段落', () => { paragraphs.push([{text:''}]); refreshTable(); });
    add.disabled = paragraphs.length >= 50; box.append(add);
  }
}

function removeRelationPart(value, message, remove) {
  const keys = keysIn(value);
  if (keys.size && !confirm(`${message}会同时删除 ${keys.size} 个空位配置，是否继续？`)) return;
  removeBlankDefinitions(keys);
  remove();
  renderStructure();
  renderBlanks();
  markChanged();
}

function renderRelation(box, content) {
  const nodesHeading = el('div'); nodesHeading.className = 'relation-heading';
  nodesHeading.append(el('h4', '节点'), el('span', `${content.nodes.length} / 40`));
  box.append(nodesHeading);
  content.nodes.forEach((node, index) => {
    const row = el('section'); row.className = 'relation-item';
    const heading = el('div'); heading.className = 'paragraph-heading';
    heading.append(el('strong', `节点 ${index + 1} · ${node.id}`));
    if (can('edit')) {
      const remove = button('删除节点', () => {
        const incident = content.edges.filter(edge => edge.source === node.id || edge.target === node.id);
        removeRelationPart([node, incident], '删除节点及其相关连线', () => {
          content.nodes.splice(index, 1);
          content.edges = content.edges.filter(edge => edge.source !== node.id && edge.target !== node.id);
        });
      });
      remove.disabled = content.nodes.length <= 2;
      heading.append(remove);
    }
    row.append(heading);
    const fields = el('div'); fields.className = 'relation-fields';
    token(fields, node.label, value => node.label = value);
    field(fields, '横坐标', node.x, value => node.x = Number(value), 'number');
    field(fields, '纵坐标', node.y, value => node.y = Number(value), 'number');
    row.append(fields); box.append(row);
  });
  if (can('edit')) {
    const add = button('添加节点', () => {
      let number = content.nodes.length + 1;
      while (content.nodes.some(node => node.id === `node_${number}`)) number++;
      content.nodes.push({id:`node_${number}`,label:{text:''},x:120,y:80+(content.nodes.length%8)*120});
      renderStructure(); markChanged();
    });
    add.disabled = content.nodes.length >= 40; box.append(add);
  }

  const edgesHeading = el('div'); edgesHeading.className = 'relation-heading';
  edgesHeading.append(el('h4', '有向连线'), el('span', `${content.edges.length} / 60`));
  box.append(edgesHeading);
  const nodeChoices = content.nodes.map(node => [node.id, node.label.text || node.id]);
  content.edges.forEach((edge, index) => {
    const row = el('section'); row.className = 'relation-item';
    const heading = el('div'); heading.className = 'paragraph-heading';
    heading.append(el('strong', `连线 ${index + 1}`));
    if (can('edit')) {
      const remove = button('删除连线', () => removeRelationPart(edge, '删除连线', () => content.edges.splice(index,1)));
      remove.disabled = content.edges.length <= 1; heading.append(remove);
    }
    row.append(heading);
    const fields = el('div'); fields.className = 'relation-fields';
    select(fields, '起点', edge.source, nodeChoices, value => edge.source = value);
    select(fields, '终点', edge.target, nodeChoices, value => edge.target = value);
    token(fields, edge.label, value => edge.label = value);
    row.append(fields); box.append(row);
  });
  if (can('edit')) {
    const add = button('添加连线', () => {
      content.edges.push({source:content.nodes[0]?.id||'',target:content.nodes[1]?.id||'',label:{text:''}});
      renderStructure(); markChanged();
    });
    add.disabled = content.edges.length >= 60; box.append(add);
  }

  const stepsHeading = el('div'); stepsHeading.className = 'relation-heading';
  stepsHeading.append(el('h4', '推导步骤'), el('span', `${content.steps.length} / 40`));
  box.append(stepsHeading);
  content.steps.forEach((paragraph, index) => {
    const row = el('section'); row.className = 'relation-item';
    const heading = el('div'); heading.className = 'paragraph-heading';
    heading.append(el('strong', `步骤 ${index + 1}`));
    if (can('edit')) heading.append(button('删除步骤', () => removeRelationPart(paragraph, '删除推导步骤', () => content.steps.splice(index,1))));
    row.append(heading);
    const fields = el('div'); fields.className = 'relation-step';
    paragraph.forEach((part, partIndex) => {
      const wrap = el('div'); wrap.className = 'relation-step-part';
      token(wrap, part, value => paragraph[partIndex] = value);
      if (can('edit')) wrap.append(button('移除片段', () => removeRelationPart(part, '移除片段', () => paragraph.splice(partIndex,1))));
      fields.append(wrap);
    });
    if (can('edit')) fields.append(button('添加片段', () => { paragraph.push({text:''}); renderStructure(); markChanged(); }));
    row.append(fields); box.append(row);
  });
  if (can('edit')) {
    const add = button('添加推导步骤', () => { content.steps.push([{text:''}]); renderStructure(); markChanged(); });
    add.disabled = content.steps.length >= 40; box.append(add);
  }
}
