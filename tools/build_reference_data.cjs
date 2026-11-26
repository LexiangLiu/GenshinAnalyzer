const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const {mkdirSync, readFileSync, renameSync, writeFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {gzipSync, gunzipSync} = require('node:zlib');

const [packageDir, tcgDir, tcgCommit, releaseDir, releaseCommit] = process.argv.slice(2);
if (!releaseDir || !/^[a-f0-9]{40}$/.test(tcgCommit || '') || !/^[a-f0-9]{40}$/.test(releaseCommit || '')) {
  throw new Error('Usage: node tools/build_reference_data.cjs <genshin-db package dir> <TCG gzip dir> <TCG revision> <release data dir> <release data revision>');
}
const packagePath = resolve(packageDir);
const db = require(packagePath);
const metadata = require(resolve(packagePath, 'package.json'));
const gameVersion = /Genshin Impact v([0-9.]+)/.exec(metadata.description)?.[1];
assert.ok(gameVersion, 'The upstream package must declare its game version.');
const rawPath = resolve(packagePath, 'src/min/data.min.json');
const upstream = require(rawPath);
const design = require(resolve(packagePath, 'src/design.json'));
const output = resolve(__dirname, '../artifact_sim/reference_data');
const languages = {zh: 'ChineseSimplified', en: 'English'};
const englishOnly = new Set(['elements', 'rarity', 'weaponmaterialtypes', 'talentmaterialtypes']);
const updateNotice = 'https://ys.mihoyo.com/main/news/detail/167205';
const digest = buffer => createHash('sha256').update(buffer).digest('hex');
const manifest = {
  schema_version: 1,
  game_version: gameVersion,
  languages: Object.keys(languages),
  sources: {
    genshin_db: {version: metadata.version, sha256: digest(readFileSync(rawPath))},
    tcg_distribution: {commit: tcgCommit, input_sha256: {}},
    release_7_2: {notice: updateNotice, commit: releaseCommit, input_sha256: {}},
  },
  categories: {},
  stats: {},
};
const generated = new Map();

function serialize(value) {
  return JSON.stringify(value, (key, entry) => key === 'base64' ? undefined : entry);
}

function save(name, value) {
  const bytes = Buffer.from(serialize(value), 'utf8');
  generated.set(name, gzipSync(bytes, {level: 9}));
  return digest(bytes);
}

function catalogRows(source, locale, kind) {
  const rows = source.data?.[locale]?.[kind];
  assert.ok(rows && Object.keys(rows).length, `${locale}: missing ${kind}`);
  const localized = source === upstream && kind === 'talents'
    ? new Map(db.talents('names', {matchCategories: true, verboseCategories: true, resultLanguage: locale}).map(row => [row.id, row]))
    : undefined;
  return Object.fromEntries(Object.keys(rows).sort().map(key => {
    const images = source.image?.[kind]?.[key];
    const url = source.url?.[kind]?.[key];
    const version = source.version?.[kind]?.[key];
    return [key, {
      ...(localized?.get(rows[key].id) ?? rows[key]),
      ...(images ? {images} : {}),
      ...(url ? {url} : {}),
      ...(version ? {version} : {}),
    }];
  }));
}

function readTcg(locale, kind) {
  const name = `${locale.toLowerCase()}-${kind}.min.json.gzip`;
  const input = readFileSync(resolve(tcgDir, name));
  manifest.sources.tcg_distribution.input_sha256[name] = digest(input);
  return JSON.parse(gunzipSync(input));
}

function releaseTable(name) {
  const input = readFileSync(resolve(releaseDir, name));
  manifest.sources.release_7_2.input_sha256[name] = digest(input);
  return JSON.parse(input);
}

const texts = {zh: releaseTable('TextMapCHS.json'), en: releaseTable('TextMapEN.json')};
const wave = releaseTable('MonsterDescribeExcelConfigData.json').find(row => row.id === 51346);
const variants = releaseTable('MonsterExcelConfigData.json').filter(row => row.describeId === wave.id);
const defaultVariant = variants.find(row => row.id === 25134601);
const monsterCurves = releaseTable('MonsterCurveExcelConfigData.json');
const waveNames = Object.fromEntries(Object.keys(languages).map(lang => [lang, texts[lang][wave.nameTextMapHash]]));
assert.deepEqual(waveNames, {zh: '浪涛锚定者', en: 'Wavebinder'});
const waveResistances = Object.fromEntries(['fire', 'elec', 'water', 'grass', 'wind', 'rock', 'ice', 'physical']
  .map(element => [element, defaultVariant[element + 'SubHurt']]));
const materialIds = new Set([101273, 101274, 112158, 112159, 112160, 113093]);
const releaseMaterials = releaseTable('MaterialExcelConfigData.json').filter(row => materialIds.has(row.id));
assert.equal(releaseMaterials.length, materialIds.size);

function releaseMaterialRows(language) {
  const locale = languages[language];
  const names = new Map();
  for (const [kind, key] of [['characters', 'mitya'], ['characters', 'valeriy'], ['weapons', 'starsealededict']]) {
    for (const items of Object.values(upstream.data[locale][kind][key].costs)) {
      for (const item of items) names.set(item.id, item.name);
    }
  }
  return Object.fromEntries(releaseMaterials.map(row => {
    assert.ok(names.get(row.id), `Missing localized cost name for ${row.id}`);
    return [`material${row.id}`, {
      id: row.id, name: names.get(row.id), icon: row.icon, category: row.materialType,
      ...(row.rankLevel ? {rarity: row.rankLevel} : {}),
      verified_in: '7.2',
    }];
  }));
}

function releaseRows(kind, language) {
  if (kind === 'enemies') {
    return {wavebinder: {
      id: wave.id, name: waveNames[language], version: '7.2', monsterType: defaultVariant.type,
      monsterId: defaultVariant.id, regions: [{key: 'novokitezhgrad', name: texts[language][435948061]}],
      icon: wave.icon, source: updateNotice, resistances: waveResistances,
      variants: variants.map(row => ({
        id: row.id, hp_base: row.hpBase, attack_base: row.attackBase, defense_base: row.defenseBase,
        drop_table_id: row.killDropId, growth_curves: row.propGrowCurves,
      })),
    }};
  }
  if (kind === 'areas') {
    return Object.fromEntries(Object.entries({novokitezhgrad: 435948061, wingspanpeak: 2845263777}).map(
      ([key, textHash]) => [key, {
        name: texts[language][textHash], name_text_hash: textHash, version: '7.2',
        regionName: language === 'zh' ? '至冬' : 'Snezhnaya', source: updateNotice,
      }],
    ));
  }
  if (kind === 'materials') return releaseMaterialRows(language);
  return {};
}

let supplementEnemy = false;
for (const kind of ['areas', ...design.standard, ...design.tcg].sort()) {
  const isTcg = design.tcg.includes(kind);
  const info = {counts: {}, sha256: {}, source_languages: {}};
  let priorIds;
  for (const [language, locale] of Object.entries(languages)) {
    const source = isTcg ? readTcg(locale, kind) : upstream;
    const fallback = language === 'zh' && englishOnly.has(kind) && !source.data?.[locale]?.[kind];
    const baseRows = kind === 'areas' ? {} : catalogRows(source, fallback ? 'English' : locale, kind);
    const additions = releaseRows(kind, language);
    if (kind === 'enemies') {
      supplementEnemy = !Object.values(baseRows).some(row => row.id === wave.id || row.name === waveNames[language]);
      if (!supplementEnemy) delete additions.wavebinder;
    }
    if (kind === 'materials') {
      const existing = new Set(Object.values(baseRows).map(row => row.id));
      for (const [key, row] of Object.entries(additions)) if (existing.has(row.id)) delete additions[key];
    }
    const rows = {...baseRows, ...additions};
    const ids = Object.entries(rows).map(([key, row]) => row.id ?? key);
    assert.equal(new Set(ids).size, ids.length, `Duplicate IDs in ${kind}/${language}`);
    if (priorIds) assert.deepEqual(new Set(ids), priorIds, `Different IDs in ${kind}/${language}`);
    priorIds = new Set(ids);
    info.counts[language] = ids.length;
    info.source_languages[language] = fallback ? 'en' : language;
    info.sha256[language] = save(`${kind}.${language}.json.gz`, rows);
  }
  manifest.categories[kind] = info;
}

// Export values from the upstream stat functions, including both sides of ascension boundaries.
for (const [kind, maxLevel] of Object.entries({characters: 100, weapons: 90, enemies: 200})) {
  const rows = db[kind]('names', {matchCategories: true, verboseCategories: true, resultLanguage: 'english'});
  const stats = {};
  for (const row of rows) {
    assert.equal(typeof row.stats, 'function', `${kind}: missing stat function for ${row.id}`);
    const levels = {};
    for (let level = 1; level <= maxLevel; level++) {
      const before = row.stats(level);
      if (before === undefined) break;
      const after = row.stats(level, '+');
      assert.ok(Object.values(before).every(Number.isFinite), `${kind}: nonfinite stats for ${row.id} level ${level}`);
      assert.ok(Object.values(after).every(Number.isFinite), `${kind}: nonfinite ascended stats for ${row.id} level ${level}`);
      levels[level] = before.ascension === after.ascension ? [before] : [before, after];
    }
    assert.ok(Object.keys(levels).length, `${kind}: empty level stats for ${row.id}`);
    stats[row.id] = levels;
  }
  if (kind === 'enemies' && supplementEnemy) {
    const growth = Object.fromEntries(defaultVariant.propGrowCurves.map(row => [row.type, row.growCurve]));
    const multiplier = (row, prop) => {
      const curve = row.curveInfos.find(info => info.type === growth[prop]);
      assert.equal(curve.arith, 'ARITH_MULTI');
      return curve.value;
    };
    stats[wave.id] = Object.fromEntries(monsterCurves.map(row => [row.level, [{
      level: row.level, monster_id: defaultVariant.id,
      hp: defaultVariant.hpBase * multiplier(row, 'FIGHT_PROP_BASE_HP'),
      attack: defaultVariant.attackBase * multiplier(row, 'FIGHT_PROP_BASE_ATTACK'),
      defense: defaultVariant.defenseBase * multiplier(row, 'FIGHT_PROP_BASE_DEFENSE'),
    }]]));
  }
  const count = Object.keys(stats).length;
  assert.equal(count, manifest.categories[kind].counts.en);
  manifest.stats[kind] = {count, sha256: save(`${kind}.stats.json.gz`, stats)};
}

mkdirSync(output, {recursive: true});
for (const [name, bytes] of generated) {
  const path = resolve(output, name);
  writeFileSync(path + '.tmp', bytes);
  renameSync(path + '.tmp', path);
}
const manifestPath = resolve(output, 'manifest.json');
writeFileSync(manifestPath + '.tmp', JSON.stringify(manifest, null, 2) + '\n', 'utf8');
renameSync(manifestPath + '.tmp', manifestPath);
console.log(JSON.stringify({gameVersion, categories: Object.keys(manifest.categories).length,
  records: Object.values(manifest.categories).reduce((total, info) => total + info.counts.zh, 0)}));
