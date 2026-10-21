/**
 * 轻量 JSON Schema 校验器（R13 teacher-prep-v2 契约测试用）。
 *
 * 覆盖 prep-contracts.schema.json（Pydantic v2 输出）实际使用的子集：
 *   type(含数组型) / enum / const / $ref($defs) / anyOf(含 null) /
 *   array+items+minItems+maxItems / object(properties+required+additionalProperties) /
 *   string pattern+minLength+maxLength / 数值 minimum。
 * 不引入 ajv 等依赖；本文件只服务于 schema 契约测试，不作为运行时校验库。
 */

export interface SchemaNode {
  type?: string | string[]
  enum?: unknown[]
  const?: unknown
  $ref?: string
  anyOf?: SchemaNode[]
  items?: SchemaNode
  minItems?: number
  maxItems?: number
  properties?: Record<string, SchemaNode>
  required?: string[]
  additionalProperties?: boolean | SchemaNode
  pattern?: string
  minLength?: number
  maxLength?: number
  minimum?: number
  maximum?: number
}

export interface Schema {
  $defs?: Record<string, SchemaNode>
  properties?: Record<string, SchemaNode>
  required?: string[]
  title?: string
  $ref?: string
}

export type Errors = string[]

/** 根路径形态的路径定位串，如 `StudentSubmission.answers[1].confidence` */
function atPath(path: string, message: string): string {
  return `${path || '$'}: ${message}`
}

function typeMatches(value: unknown, type: string): boolean {
  switch (type) {
    case 'string':
      return typeof value === 'string'
    case 'integer':
      return Number.isInteger(value)
    case 'number':
      return typeof value === 'number'
    case 'boolean':
      return typeof value === 'boolean'
    case 'array':
      return Array.isArray(value)
    case 'object':
      return typeof value === 'object' && value !== null && !Array.isArray(value)
    case 'null':
      return value === null
    default:
      return true // 未知 type 放行（向前兼容）
  }
}

function validateNode(
  value: unknown,
  node: SchemaNode,
  defs: Record<string, SchemaNode>,
  path: string,
  errors: Errors,
): void {
  if (node.$ref) {
    const name = node.$ref.split('/').pop() as string
    const def = defs[name]
    if (def) validateNode(value, def, defs, path, errors)
    return
  }
  if (node.const !== undefined) {
    if (value !== node.const) errors.push(atPath(path, `const 不匹配 ${JSON.stringify(node.const)}`))
    return
  }
  if (node.enum) {
    if (!node.enum.some((v) => v === value)) {
      errors.push(atPath(path, `不在枚举 ${JSON.stringify(node.enum)} 内`))
    }
    return
  }
  if (node.anyOf) {
    const subErrors = new Set<string>()
    let anyValid = false
    for (const sub of node.anyOf) {
      const local: Errors = []
      validateNode(value, sub, defs, path, local)
      if (local.length === 0) {
        anyValid = true
        break
      }
      subErrors.add(local[0])
    }
    if (!anyValid) errors.push(atPath(path, `anyOf 均不匹配: ${[...subErrors].join(' | ')}`))
    return
  }

  const type = node.type
  if (type === undefined) return
  const types = Array.isArray(type) ? type : [type]
  if (!types.some((t) => typeMatches(value, t))) {
    errors.push(atPath(path, `类型应为 ${types.join('|')}`))
    return
  }

  if (typeof value === 'string') {
    if (node.pattern !== undefined && new RegExp(node.pattern).test(value) === false) {
      errors.push(atPath(path, `pattern 不匹配 ${node.pattern}`))
    }
    if (node.minLength !== undefined && value.length < node.minLength) {
      errors.push(atPath(path, `短于 minLength ${node.minLength}`))
    }
    if (node.maxLength !== undefined && value.length > node.maxLength) {
      errors.push(atPath(path, `长于 maxLength ${node.maxLength}`))
    }
  }

  if (Array.isArray(value) && node.items) {
    if (node.minItems !== undefined && value.length < node.minItems) {
      errors.push(atPath(path, `少于最小项数 ${node.minItems}`))
    }
    if (node.maxItems !== undefined && value.length > node.maxItems) {
      errors.push(atPath(path, `多于最大项数 ${node.maxItems}`))
    }
    value.forEach((item, i) => validateNode(item, node.items as SchemaNode, defs, `${path}[${i}]`, errors))
  } else if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    const record = value as Record<string, unknown>
    if (node.properties) {
      for (const [key, sub] of Object.entries(node.properties)) {
        if (key in record) {
          validateNode(record[key], sub, defs, path ? `${path}.${key}` : key, errors)
        } else if (node.required && node.required.includes(key)) {
          errors.push(atPath(path ? `${path}.${key}` : key, '缺少必填字段'))
        }
      }
    } else if (node.additionalProperties && typeof node.additionalProperties === 'object') {
      for (const [key, v] of Object.entries(record)) {
        validateNode(v, node.additionalProperties, defs, path ? `${path}.${key}` : key, errors)
      }
    }
    if (node.minimum !== undefined && typeof value === 'number' && value < node.minimum) {
      errors.push(atPath(path, `小于最小值 ${node.minimum}`))
    }
    if (node.maximum !== undefined && typeof value === 'number' && value > node.maximum) {
      errors.push(atPath(path, `大于最大值 ${node.maximum}`))
    }
  } else if (typeof value === 'number' && node.minimum !== undefined && value < node.minimum) {
    errors.push(atPath(path, `小于最小值 ${node.minimum}`))
  } else if (typeof value === 'number' && node.maximum !== undefined && value > node.maximum) {
    errors.push(atPath(path, `大于最大值 ${node.maximum}`))
  }
}

/** 校验 data 是否符合 schema；返回错误列表（空数组 = 通过）。 */
export function validateBySchema(data: unknown, schema: Schema): Errors {
  const errors: Errors = []
  const defs = schema.$defs ?? {}
  validateNode(data, schema as SchemaNode, defs, '', errors)
  return errors
}

/** 规范序列化：按 key 排序 + 紧凑分隔，用于 Python/TS 互证哈希（与后端 _canonical 一致）。 */
export function canonicalSerialize(value: unknown): string {
  if (value === null) return 'null'
  if (Array.isArray(value)) return `[${value.map(canonicalSerialize).join(',')}]`
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    const keys = Object.keys(record).sort()
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalSerialize(record[k])}`).join(',')}}`
  }
  if (typeof value === 'string') return JSON.stringify(value)
  return String(value)
}