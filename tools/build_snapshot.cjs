const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const {readFileSync, writeFileSync} = require('node:fs');
const {resolve} = require('node:path');

const [packageDir, pmfPath, excelDir, gameVersion] = process.argv.slice(2);
if (!gameVersion) {
  throw new Error('Usage: node tools/build_snapshot.cjs <genshin-db package dir> <PMF JSON> <ExcelBinOutput dir> <game version>');
}
const db = require(resolve(packageDir));
const npmVersion = require(resolve(packageDir, 'package.json')).version;
const sources = {};

function read(path, sourceName) {
  const input = readFileSync(path);
  sources[sourceName] = createHash('sha256').update(input).digest('hex');
  return JSON.parse(input.toString('utf8').replace(/^\uFEFF/, ''));
}

const pmf = read(pmfPath, 'genshin_artifact_5star_domain_pmf.json');
const levels = read(resolve(excelDir, 'ReliquaryLevelExcelConfigData.json'), 'ReliquaryLevelExcelConfigData.json');
const affixes = read(resolve(excelDir, 'ReliquaryAffixExcelConfigData.json'), 'ReliquaryAffixExcelConfigData.json');
const statMap = {
  HP: 'HP', HP_PERCENT: 'HP_PERCENT', ATTACK: 'ATK', ATTACK_PERCENT: 'ATK_PERCENT',
  DEFENSE: 'DEF', DEFENSE_PERCENT: 'DEF_PERCENT', CRITICAL: 'CRIT_RATE', CRITICAL_HURT: 'CRIT_DMG',
  CHARGE_EFFICIENCY: 'ENERGY_RECHARGE', HEAL_ADD: 'HEALING_BONUS', ELEMENT_MASTERY: 'ELEMENTAL_MASTERY',
  FIRE_ADD_HURT: 'PYRO_DMG_BONUS', ELEC_ADD_HURT: 'ELECTRO_DMG_BONUS', WATER_ADD_HURT: 'HYDRO_DMG_BONUS',
  WIND_ADD_HURT: 'ANEMO_DMG_BONUS', ROCK_ADD_HURT: 'GEO_DMG_BONUS', GRASS_ADD_HURT: 'DENDRO_DMG_BONUS',
  ICE_ADD_HURT: 'CRYO_DMG_BONUS', PHYSICAL_ADD_HURT: 'PHYSICAL_DMG_BONUS',
};
const statKey = raw => statMap[raw.replace('FIGHT_PROP_', '')];
const inGameUnits = (stat, value) => pmf.stat_units[stat] === 'percent' ? Number((value * 100).toFixed(8)) : value;
const mainStats = Object.fromEntries([...new Set(Object.values(pmf.main_stat_pmf_by_slot).flatMap(Object.keys))].map(stat => [stat, []]));
const fiveStarLevels = levels.filter(row => row.rank === 5).sort((a, b) => a.level - b.level);
assert.deepEqual(fiveStarLevels.map(row => row.level), Array.from({length: 21}, (_, index) => index + 1));
for (const row of fiveStarLevels) {
  for (const {propType, value} of row.addProps) {
    const stat = statKey(propType);
    if (mainStats[stat]) mainStats[stat].push(inGameUnits(stat, value));
  }
}
assert.ok(Object.values(mainStats).every(values => values.length === 21));

const rollValues = {};
for (const row of affixes.filter(row => row.depotId === 501).sort((a, b) => a.id - b.id)) {
  const stat = statKey(row.propType);
  assert.ok(pmf.minor_affix_type_weights[stat]);
  (rollValues[stat] ??= []).push(inGameUnits(stat, row.propValue));
}
assert.deepEqual(Object.keys(rollValues).sort(), Object.keys(pmf.minor_affix_type_weights).sort());
assert.ok(Object.values(rollValues).every(values => values.length === 4));

const query = (folder, language) => db[folder]('names', {
  matchCategories: true, verboseCategories: true, resultLanguage: language,
});
const slots = Object.keys(pmf.slot_pmf);
const englishSets = new Map(query('artifacts', 'english').map(set => [set.id, set]));
const sets = query('artifacts', 'chinese').sort((a, b) => a.id - b.id).map(set => {
  const en = englishSets.get(set.id);
  return {
    id: set.id, name: set.name, name_en: en.name, introduced: set.version, rarities: set.rarityList,
    pieces: Object.fromEntries(slots.filter(slot => set[slot.toLowerCase()]).map(slot => [slot, set[slot.toLowerCase()].name])),
    pieces_en: Object.fromEntries(slots.filter(slot => en[slot.toLowerCase()]).map(slot => [slot, en[slot.toLowerCase()].name])),
    effects: Object.fromEntries([1, 2, 4].filter(n => set[`effect${n}Pc`]).map(n => [n, set[`effect${n}Pc`]])),
    effects_en: Object.fromEntries([1, 2, 4].filter(n => en[`effect${n}Pc`]).map(n => [n, en[`effect${n}Pc`]])),
  };
});
assert.equal(new Set(sets.map(set => set.id)).size, sets.length);
assert.equal(sets.length, englishSets.size);
const fiveStarSets = new Map(sets.filter(set => set.rarities.includes(5)).map(set => [set.name, set.id]));
const domainMap = new Map();
for (const domain of query('domains', 'chinese').filter(domain => domain.domainType === 'UI_ABYSSUS_RELIC')) {
  const names = [...new Set(domain.rewardPreview.filter(reward => reward.rarity === 5).map(reward => reward.name))];
  if (!names.length) continue;
  const setIds = names.map(name => fiveStarSets.get(name)).sort((a, b) => a - b);
  assert.equal(setIds.length, 2, domain.name);
  assert.ok(setIds.every(Number.isInteger), domain.name);
  const entry = domainMap.get(domain.entranceId) ?? {id: domain.entranceId, name: domain.entranceName, set_ids: setIds};
  assert.deepEqual(entry.set_ids, setIds, domain.name);
  domainMap.set(entry.id, entry);
}
const domains = [...domainMap.values()].sort((a, b) => a.id - b.id);
const englishCharacters = new Map(query('characters', 'english').map(character => [character.id, character]));
const characters = query('characters', 'chinese').sort((a, b) => a.id - b.id).map(character => ({
  id: character.id, name: character.name, name_en: englishCharacters.get(character.id).name,
  element: character.elementType, weapon: character.weaponType, rarity: character.rarity,
  region: character.region || null, region_en: englishCharacters.get(character.id).region || null,
}));
const snapshot = {
  game_version: gameVersion,
  sources: {genshin_db: npmVersion, input_sha256: sources},
  sets, domains, characters,
  slot_weights: pmf.slot_pmf,
  main_stat_weights: pmf.main_stat_pmf_by_slot,
  initial_count_weights: pmf.initial_substat_count_pmf,
  substat_weights: pmf.minor_affix_type_weights,
  tier_weights: Object.values(pmf.minor_affix_value_tier_pmf),
  upgrade_slot_weights: Object.values(pmf.enhancement.upgrade_slot_pmf_when_4_substats),
  upgrade_tier_weights: Object.values(pmf.enhancement.upgrade_value_tier_pmf),
  main_stat_values: mainStats,
  substat_values: rollValues,
  stat_units: pmf.stat_units,
};
writeFileSync(resolve(__dirname, '../artifact_sim/data/data.json'), JSON.stringify(snapshot, null, 2) + '\n', 'utf8');
console.log(JSON.stringify({sets: sets.length, fiveStarSets: fiveStarSets.size, domains: domains.length}));
