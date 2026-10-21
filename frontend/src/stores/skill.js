import { defineStore } from 'pinia'
import { SKILL_ID_BY_KEY, SKILL_KEY_BY_ID } from '@/config/skills'

/**
 * 点亮技能集合（多选，会话级状态）。
 * activeKeys 为空 = 自由对话（智能路由）；非 chat 技能可叠加多选，点 chat 清空其他。
 */
export const useSkillStore = defineStore('skill', {
  state: () => ({ activeKeys: [] }),
  getters: {
    /** 是否自由对话态（未点亮任何技能） */
    isChat: (s) => s.activeKeys.length === 0,
    /** 后端契约：点亮集合 → skill_id 列表（chat 为默认态，不下发） */
    skillIds: (s) => s.activeKeys.map((k) => SKILL_ID_BY_KEY[k]).filter(Boolean),
  },
  actions: {
    /** 切换点亮：chat = 清空其他；非 chat 未点则点亮、已点则熄灭（多选） */
    toggle(key) {
      if (key === 'chat') {
        this.activeKeys = []
        return
      }
      const i = this.activeKeys.indexOf(key)
      if (i >= 0) this.activeKeys.splice(i, 1)
      else this.activeKeys.push(key)
    },
    /** 熄灭单个技能（chip 上的 ×） */
    remove(key) {
      this.activeKeys = this.activeKeys.filter((k) => k !== key)
    },
    reset() {
      this.activeKeys = []
    },
    /** 新会话：回到默认态（自由对话 = 智能路由） */
    resetDefault() {
      this.activeKeys = []
    },
    /**
     * 路由结果自动更正点亮集合（以 meta.skill 为准）。
     * 有变化时更正并返回目标 key（'' = 自由对话）；无变化返回 null（调用方只在非 null 时提示）。
     */
    correctTo(skillId) {
      const key = SKILL_KEY_BY_ID[skillId] || ''
      if (key && this.activeKeys.length === 1 && this.activeKeys[0] === key) return null
      if (!key && this.activeKeys.length === 0) return null
      this.activeKeys = key ? [key] : []
      return key
    },
  },
})
