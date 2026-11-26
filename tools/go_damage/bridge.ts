import { readFileSync } from 'node:fs'
import type { IConditionalData } from '@genshin-optimizer/game-opt/engine'
import {
  allCharacterKeys, allElementWithPhyKeys, allWeaponKeys, ascensionMaxLevel, talentLimits,
} from '@genshin-optimizer/gi/consts'
import type { CharacterKey, ElementWithPhyKey, WeaponKey } from '@genshin-optimizer/gi/consts'
import { getCharStat, getWeaponStat } from '@genshin-optimizer/gi/stats'
import type { ICharacter, IWeapon } from '@genshin-optimizer/gi/good'
import {
  charData, conditionalEntries, conditionals, enemyDebuff, formulaCatalog,
  genshinCalculatorWithEntries, lookupFormulaRef, own, ownBuff, ownTag,
  pandoContextEntries, readStat, teamData, userBuff, weaponData, withMember,
} from '@genshin-optimizer/gi/formula'
import type { FormulaCatalog, Sheet } from '@genshin-optimizer/gi/formula'
import type { StatKey } from '@genshin-optimizer/gi/dm'
import { read } from '@genshin-optimizer/pando/engine'

const members = ['0'] as const
const catalog: FormulaCatalog = formulaCatalog
const conditionCatalog: Partial<Record<Sheet, Record<string, IConditionalData>>> = conditionals
const elements: readonly string[] = allElementWithPhyKeys
const allowedDamageModes = { average: 'avg', noncrit: 'nonCrit' } as const

function knownKeys(input: Record<string, unknown>, allowed: readonly string[], section: string) {
  for (const key of Object.keys(input)) {
    if (!allowed.includes(key)) throw new Error(`Unknown input: ${section}.${key}`)
  }
}

function object(value: unknown, name: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${name} must be a JSON object`)
  }
  return value as Record<string, unknown>
}

function number(value: unknown, name: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${name} must be finite`)
  }
  return value
}

function integer(value: unknown, name: string, min: number, max: number): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} must be an integer from ${min} to ${max}`)
  }
  return value
}

function resolveKey<T extends string>(value: unknown, keys: readonly T[], name: string): T {
  const key = typeof value === 'string' && keys.find(item => item.toLowerCase() === value.trim().toLowerCase())
  if (!key) throw new Error(`Unknown ${name}: ${String(value)}`)
  return key
}

function readLevel(input: Record<string, unknown>, name: string, highestAscension = 6) {
  const ascension = integer(input.ascension, `${name}.ascension`, 0, highestAscension) as ICharacter['ascension']
  const level = integer(
    input.level, `${name}.level`, ascensionMaxLevel[ascension - 1] ?? 1, ascensionMaxLevel[ascension],
  )
  return { level, ascension }
}

function readCharacter(input: Record<string, unknown>): ICharacter {
  const key = resolveKey(input.character, allCharacterKeys, 'character')
  const levels = readLevel(input, 'character')
  const talents = object(input.talents, 'talents')
  knownKeys(talents, ['auto', 'skill', 'burst'], 'talents')
  const max = talentLimits[levels.ascension]
  return {
    key, ...levels,
    constellation: integer(input.constellation, 'constellation', 0, 6) as ICharacter['constellation'],
    talent: {
      auto: integer(talents.auto, 'talents.auto', 1, max),
      skill: integer(talents.skill, 'talents.skill', 1, max),
      burst: integer(talents.burst, 'talents.burst', 1, max),
    },
  }
}

function readWeapon(value: unknown, character: CharacterKey): IWeapon {
  const input = object(value, 'weapon')
  knownKeys(input, ['key', 'level', 'ascension', 'refinement', 'conditions'], 'weapon')
  const key: WeaponKey = resolveKey(input.key, allWeaponKeys, 'weapon')
  const weapon = getWeaponStat(key)
  if (weapon.weaponType !== getCharStat(character).weaponType) throw new Error('Weapon type does not match character')
  const refinable = weapon.rarity > 2
  return {
    key, ...readLevel(input, 'weapon', refinable ? 6 : 4),
    refinement: integer(input.refinement ?? 1, 'weapon.refinement', 1, refinable ? 5 : 1) as IWeapon['refinement'],
    location: character, lock: false,
  }
}

function bonusEntries(input: unknown) {
  return Object.entries(object(input, 'bonus_stats')).map(([key, value]) => {
    const direct = Object.hasOwn(ownTag.premod, key)
    const elemental = elements.some(element => key === `${element}_dmg_` || key === `${element}_res_`)
    if (!direct && !elemental) throw new Error(`Unknown bonus stat: ${key}`)
    return readStat(userBuff.premod, key as StatKey).add(number(value, `bonus_stats.${key}`))
  })
}

function conditionEntries(sheet: Sheet, input: unknown) {
  const definitions = conditionCatalog[sheet] ?? {}
  const add = conditionalEntries(sheet, members[0], null)
  return Object.entries(object(input, 'conditions')).map(([key, value]) => {
    if (!Object.hasOwn(definitions, key)) throw new Error(`Unknown condition: ${sheet}.${key}`)
    const rule = definitions[key]
    if (rule.type === 'bool' && typeof value === 'boolean') return add(key, Number(value))
    if (rule.type === 'list' && typeof value === 'string') {
      const index = rule.list.indexOf(value)
      // Upstream list nodes reserve index zero for the unset condition.
      if (index !== -1) return add(key, index + 1)
    }
    if (rule.type === 'num' && typeof value === 'number' && Number.isFinite(value)
      && (rule.min === undefined || value >= rule.min)
      && (rule.max === undefined || value <= rule.max)
      && (!rule.int_only || Number.isInteger(value))) return add(key, value)
    throw new Error(`Invalid value for condition ${sheet}.${key}`)
  })
}

function enemyEntries(raw: unknown) {
  const input = object(raw, 'enemy')
  knownKeys(input, ['level', 'resistance', 'resistances', 'defense_reduction', 'defense_ignore'], 'enemy')
  const resistance = number(input.resistance ?? 0.1, 'enemy.resistance')
  return [
    enemyDebuff.common.lvl.add(integer(input.level ?? 90, 'enemy.level', 1, 200)),
    enemyDebuff.common.preRes.add(resistance),
    enemyDebuff.common.defRed_.add(number(input.defense_reduction ?? 0, 'enemy.defense_reduction')),
    enemyDebuff.common.defIgn.add(number(input.defense_ignore ?? 0, 'enemy.defense_ignore')),
    ...Object.entries(object(input.resistances ?? {}, 'enemy.resistances')).map(([element, value]) => {
      if (!elements.includes(element)) throw new Error(`Unknown resistance element: ${element}`)
      const stat = enemyDebuff.common.preRes.with('ele', element as ElementWithPhyKey)
      return stat.add(number(value, `enemy.resistances.${element}`) - resistance)
    }),
    enemyDebuff.reaction.cata.add(''),
    enemyDebuff.reaction.amp.add(''),
  ]
}

function attackCatalog(value: unknown) {
  const character = resolveKey(value, allCharacterKeys, 'character')
  return {
    character,
    attacks: Object.values(catalog[character] ?? {})
      .filter(entry => entry.dims.dmg)
      .map(entry => ({ name: entry.name, category: entry.category ?? null })),
    conditions: conditionCatalog[character] ?? {},
  }
}

function calculate(input: Record<string, unknown>) {
  knownKeys(input, [
    'character', 'level', 'ascension', 'constellation', 'talents', 'weapon',
    'bonus_stats', 'conditions', 'enemy', 'mode', 'attacks',
  ], 'request')
  const character = readCharacter(input)
  const weapon = input.weapon === undefined ? undefined : readWeapon(input.weapon, character.key)
  const mode = input.mode ?? 'average'
  if (typeof mode !== 'string' || !Object.hasOwn(allowedDamageModes, mode)) {
    throw new Error('mode must be average or noncrit')
  }
  const requested = input.attacks
  if (!Array.isArray(requested) || !requested.length || requested.some(name => typeof name !== 'string')) {
    throw new Error('attacks must be a non-empty array of upstream attack names')
  }
  const calculator = genshinCalculatorWithEntries([
    ...teamData(members),
    ...pandoContextEntries({ memberKeys: members }),
    ...withMember(
      members[0], ...charData(character), ...(weapon ? weaponData(weapon) : []),
      ...bonusEntries(input.bonus_stats ?? {}),
    ),
    ...conditionEntries(character.key, input.conditions ?? {}),
    ...(weapon ? conditionEntries(weapon.key, object(input.weapon, 'weapon').conditions ?? {}) : []),
    ...enemyEntries(input.enemy ?? {}),
    ownBuff.common.critMode.add(allowedDamageModes[mode as keyof typeof allowedDamageModes]),
  ]).withTag({ src: members[0] })
  const attacks = requested.map(name => {
    const formula = lookupFormulaRef({ sheet: character.key, name, dim: 'dmg' })
    if (!formula) throw new Error(`Unknown damage formula for ${character.key}: ${name}`)
    const { val, meta } = calculator.compute(read(formula.tag))
    return {
      name,
      element: meta.tag?.ele ?? formula.tag.ele ?? null,
      damage: number(val, `${name} damage`),
    }
  })
  return {
    character: character.key, mode,
    effective_talents: {
      auto: calculator.compute(own.char.auto).val,
      skill: calculator.compute(own.char.skill).val,
      burst: calculator.compute(own.char.burst).val,
    },
    panel: {
      hp: calculator.compute(own.final.hp).val,
      atk: calculator.compute(own.final.atk).val,
      def: calculator.compute(own.final.def).val,
      critRate_: calculator.compute(own.common.cappedCritRate_).val,
      critDMG_: calculator.compute(own.final.critDMG_).val,
      eleMas: calculator.compute(own.final.eleMas).val,
    },
    attacks,
  }
}

try {
  if (Number(process.versions.node.split('.')[0]) < 24) throw new Error('Node.js 24 or newer is required')
  const request = object(JSON.parse(readFileSync(0, 'utf8')), 'request')
  const payload = object(request.payload, 'payload')
  let result: unknown
  if (request.action === 'calculate') result = calculate(payload)
  else if (request.action === 'formulas') result = attackCatalog(payload.character)
  else throw new Error(`Unknown action: ${String(request.action)}`)
  process.stdout.write(JSON.stringify(result))
} catch (error) {
  process.stderr.write((error instanceof Error ? error.message : String(error)) + '\n')
  process.exitCode = 1
}
