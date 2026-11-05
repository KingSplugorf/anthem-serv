const element = (id) => document.getElementById(id);
let state = null;
let busy = false;

function message(text) { element('status').textContent = text; }
function base() {
  return '/local/progression/characters/' + encodeURIComponent(element('pilot').value);
}
async function api(path, method = 'GET', body) {
  const response = await fetch(path, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw Error(value.error || `Request failed (${response.status})`);
  return value;
}

async function mutate(operation, payload) {
  // Keep the same operation ID after a lost response or page reload. A second
  // click must not debit again when the first request actually reached BIGS.
  const key = 'anthem-progression/' + JSON.stringify([base(), operation, payload]);
  let transactionId = sessionStorage.getItem(key);
  if (!transactionId) {
    transactionId = crypto.randomUUID();
    sessionStorage.setItem(key, transactionId);
  }
  const result = await api(base() + '/' + operation, 'POST', { ...payload, transactionId });
  sessionStorage.removeItem(key);
  return result;
}

function materialName(key) {
  const rarities = ['', 'common', 'uncommon', 'rare', 'epic', 'masterwork', 'legendary'];
  return key.startsWith('ember_') ? `${rarities[Number(key.slice(6))] || 'unknown'} embers` : key;
}
function costs() {
  const recipe = state?.recipes.find((recipe) => recipe.recipeId === element('recipe').value);
  element('cost').textContent = recipe
    ? Object.entries(recipe.costs).map(([key, amount]) => `${amount} ${materialName(key)}`).join(' + ')
    : 'No compatible recipes.';
}
function button(label, action) {
  const result = document.createElement('button');
  result.textContent = label;
  result.disabled = busy;
  result.dataset.itemAction = '1';
  result.onclick = () => run(action);
  return result;
}
function renderItems() {
  element('items').replaceChildren();
  if (!state.items.length) {
    element('items').textContent = 'No earned equipment yet.';
    return;
  }
  const table = document.createElement('table');
  const header = table.insertRow();
  for (const title of ['Item / instance', 'Level', 'Rarity', '']) {
    const cell = document.createElement('th');
    cell.textContent = title;
    header.append(cell);
  }
  for (const item of state.items) {
    const row = table.insertRow();
    for (const value of [`${item.gameItemId} / ${item.uid}`, item.level, item.rarity]) {
      row.insertCell().textContent = String(value);
    }
    row.insertCell().append(button('Salvage', () => mutate('salvage', { uids: [item.uid] })));
  }
  element('items').append(table);
}
function updateButtons() {
  const loaded = state?.characterId === element('pilot').value;
  const pending = (state?.backpack.length || 0) + (state?.mailbox.length || 0);
  element('claim').disabled = busy || !loaded || !pending || (state.items.length >= 1000 && !state.backpack.length);
  element('craft').disabled = busy || !loaded || !state?.recipes.length;
  document.querySelectorAll('[data-item-action]').forEach((control) => { control.disabled = busy || !loaded; });
}
async function load() {
  if (!element('pilot').value) return;
  state = await api(base());
  element('level').textContent = `Level ${state.level}`;
  element('xp').textContent = state.nextLevelXp
    ? `${state.xp.toLocaleString()} XP · ${state.nextLevelXp - state.xp} XP to next level`
    : `${state.xp.toLocaleString()} XP · Maximum level`;
  element('bar').max = state.nextLevelXp ? state.nextLevelXp - state.levelFloorXp : 1;
  element('bar').value = state.nextLevelXp ? state.xp - state.levelFloorXp : 1;
  element('materials').textContent = Object.entries(state.materials)
    .map(([key, value]) => `${materialName(key)}: ${value}`).join(' · ') || 'No crafting materials yet.';
  const selected = element('recipe').value;
  element('recipe').replaceChildren();
  for (const recipe of state.recipes) {
    const option = document.createElement('option');
    option.value = recipe.recipeId;
    option.textContent = recipe.name;
    element('recipe').append(option);
  }
  if (state.recipes.some((recipe) => recipe.recipeId === selected)) element('recipe').value = selected;
  costs();
  renderItems();
  element('pending').textContent = `${state.backpack.length} backpack item(s), ${state.mailbox.length} deferred reward(s).`
    + (state.items.length >= 1000 ? ' Inventory full — salvage equipment before claiming rewards.' : '');
  updateButtons();
}
async function run(action, success = 'Saved.') {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button,select').forEach((control) => { control.disabled = true; });
  try {
    await action();
    await load();
    message(state ? success : 'Create a pilot in the launcher first.');
  } catch (error) {
    message(error.message);
  } finally {
    busy = false;
    document.querySelectorAll('button,select').forEach((control) => { control.disabled = false; });
    updateButtons();
  }
}

element('recipe').onchange = costs;
element('pilot').onchange = () => run(load, 'Loaded pilot.');
element('refresh').onclick = () => run(load, 'Updated.');
element('craft').onclick = () => run(() => mutate('craft', { recipeId: element('recipe').value }));
element('claim').onclick = () => run(() => api(base() + '/claim', 'POST', {}));
run(async () => {
  const overview = await api('/local/progression');
  for (const pilot of overview.pilots) {
    const option = document.createElement('option');
    option.value = pilot.id;
    option.textContent = pilot.name || `Pilot ${pilot.id}`;
    element('pilot').append(option);
  }
}, 'Ready.');
