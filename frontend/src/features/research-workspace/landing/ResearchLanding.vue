<template>
  <div class="rl-page" data-research-app>
    <ResearchHomeHeader active="home" />

    <main class="rl-main">
      <section class="rl-hero">
        <div class="rl-hero-copy">
          <div class="rl-eyebrow"><span></span> RESEARCH WORKSPACE</div>
          <h1>阅读、写作与验证，<br /><em>在同一个项目里发生。</em></h1>
          <p class="rl-lead">
            把文献、笔记、公式和草稿放进一个 Research Project。读到哪里、论证到哪一步、下一步该做什么，都不再散落在多个工具里。
          </p>
          <div class="rl-cta-row">
            <button type="button" class="rl-cta rl-cta--primary" :disabled="creating" @click="enterWorkspace">
              {{ creating ? '正在准备…' : '进入科研工作台' }} <span>→</span>
            </button>
            <button type="button" class="rl-cta" :disabled="creating" @click="createAndOpen(lastWorkspace)">
              ＋ 新建项目
            </button>
          </div>
          <div class="rl-trust">
            <span>真实文献</span><i>·</i><span>真实 LaTeX 编译</span><i>·</i><span>证据可回溯</span>
          </div>
        </div>

        <div class="rl-paper-wrap" aria-label="最近项目">
          <div class="rl-paper-shadow"></div>
          <article class="rl-paper">
            <header class="rl-paper-head">
              <span class="rl-paper-mark">π</span>
              <span>PROJECT MEMORY</span>
              <span class="rl-paper-state">{{ lastProject ? '最近项目' : '尚未开始' }}</span>
            </header>
            <template v-if="lastProject">
              <h2 class="rl-paper-title">{{ lastProject.name }}</h2>
              <p class="rl-paper-desc">{{ lastProject.description || '从一篇论文、一个公式或一段论证开始。' }}</p>
              <div class="rl-paper-counts">
                <span><b>{{ countOf(lastProject, 'papers') }}</b> 文献</span>
                <span><b>{{ countOf(lastProject, 'drafts') }}</b> 草稿</span>
                <span><b>{{ countOf(lastProject, 'notes') }}</b> 笔记</span>
              </div>
              <div class="rl-paper-rule"></div>
              <div class="rl-paper-next">
                <div>
                  <span class="rl-paper-label">下一步</span>
                  <b>{{ lastWorkspace === 'write' ? '继续写作与编译' : '继续阅读与摘录' }}</b>
                </div>
                <button type="button" @click="openProject(lastProject, lastWorkspace)">继续 →</button>
              </div>
              <div class="rl-paper-formula" aria-hidden="true">
                <span>∂u/∂t = α∇²u</span>
                <i>读 → 摘 → 证 → 写 → 核</i>
              </div>
            </template>
            <template v-else>
              <h2 class="rl-paper-title">你的第一个 Research Project</h2>
              <p class="rl-paper-desc">一个项目可以同时容纳文献、笔记、草稿与 Agent 对话。创建后从导入第一篇论文开始。</p>
              <div class="rl-paper-steps">
                <span><b>01</b> 导入文献</span>
                <span><b>02</b> 阅读与摘录</span>
                <span><b>03</b> 写作与验证</span>
              </div>
              <button type="button" class="rl-paper-create" :disabled="creating" @click="createAndOpen('read')">创建第一个项目 →</button>
            </template>
          </article>
        </div>
      </section>

      <section class="rl-start">
        <div class="rl-section-head">
          <div>
            <span class="rl-kicker">FROM HERE</span>
            <h2>今天推进哪项研究？</h2>
          </div>
          <p>三种开始方式，都会进入同一个项目上下文。</p>
        </div>

        <div class="rl-paths">
          <button type="button" class="rl-path" :disabled="creating" @click="startRead">
            <span class="rl-path-no">01</span>
            <span class="rl-path-ic">📖</span>
            <span class="rl-path-title">读论文</span>
            <span class="rl-path-desc">导入 PDF，边读边记；选中段落或公式，直接问 Research Agent。</span>
            <span class="rl-path-go">继续阅读 <i>→</i></span>
          </button>
          <button type="button" class="rl-path" :disabled="creating" @click="startWrite">
            <span class="rl-path-no">02</span>
            <span class="rl-path-ic">✍️</span>
            <span class="rl-path-title">写论文</span>
            <span class="rl-path-desc">LaTeX 编辑器、真实编译、PDF 预览；让论证和引用一起生长。</span>
            <span class="rl-path-go">继续写作 <i>→</i></span>
          </button>
          <button type="button" class="rl-path" :disabled="creating" @click="startAgent">
            <span class="rl-path-no">03</span>
            <span class="rl-path-ic">✦</span>
            <span class="rl-path-title">问 Agent</span>
            <span class="rl-path-desc">公式检查、引用核查、翻译润色；结论带证据，修改先确认。</span>
            <span class="rl-path-go">打开 Research Agent <i>→</i></span>
          </button>
        </div>

        <div class="rl-chain" aria-label="科研工作链路">
          <span>读</span><i>→</i><span>摘</span><i>→</i><span>证</span><i>→</i><span>写</span><i>→</i><span>核</span>
          <em>一条链路，一个项目上下文</em>
        </div>
      </section>

      <section class="rl-projects">
        <div class="rl-section-head">
          <div>
            <span class="rl-kicker">RECENT PROJECTS</span>
            <h2>最近项目</h2>
          </div>
          <button v-if="projects.length" type="button" class="rl-mini-btn" :disabled="creating" @click="createAndOpen(lastWorkspace)">＋ 新建项目</button>
        </div>

        <div v-if="loading" class="rl-project-skeleton">
          <span></span><span></span><span></span>
        </div>
        <div v-else-if="error" class="rl-state rl-state--error">
          <b>项目列表加载失败</b>
          <p>{{ error }}</p>
          <button type="button" @click="refresh">重试</button>
          <button type="button" class="ghost" @click="enterWorkspace">仍然进入工作台</button>
        </div>
        <div v-else-if="!projects.length" class="rl-state">
          <b>还没有科研项目</b>
          <p>创建项目后，文献、笔记、草稿和 Agent 对话都会归到这里。</p>
          <button type="button" :disabled="creating" @click="createAndOpen('read')">新建第一个项目</button>
        </div>
        <div v-else class="rl-project-grid">
          <article v-for="p in projects.slice(0, 6)" :key="p.id" class="rl-project">
            <div class="rl-project-top">
              <span class="rl-project-ic">π</span>
              <span class="rl-project-time">{{ fmtTime(p.updated_at) }}</span>
            </div>
            <h3>{{ p.name }}</h3>
            <p>{{ p.description || '暂无项目描述' }}</p>
            <div class="rl-project-counts">
              <span>{{ countOf(p, 'papers') }} 文献</span>
              <span>{{ countOf(p, 'drafts') }} 草稿</span>
              <span>{{ countOf(p, 'notes') }} 笔记</span>
            </div>
            <div class="rl-project-actions">
              <button type="button" @click="openProject(p, 'read')">阅读</button>
              <button type="button" @click="openProject(p, 'write')">写作</button>
            </div>
          </article>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ResearchHomeHeader from './ResearchHomeHeader.vue'
import { useRwProjectStore } from '../stores/project'
import { newIdempotencyKey } from '@/api/idempotency'
import type { ResearchProject, WorkspaceMode } from '../api/types'
import '../tokens/research-tokens.css'

const LAST_PROJECT_KEY = 'ma_research_last_project'
const LAST_WORKSPACE_KEY = 'ma_research_last_workspace'

const router = useRouter()
const projectStore = useRwProjectStore()
const loading = ref(true)
const creating = ref(false)
const error = ref('')

const projects = computed(() => projectStore.list)
const lastProject = computed(() => {
  const id = localStorage.getItem(LAST_PROJECT_KEY) || ''
  return projects.value.find((p) => p.id === id) || projects.value[0] || null
})
const lastWorkspace = computed<WorkspaceMode>(() =>
  localStorage.getItem(LAST_WORKSPACE_KEY) === 'write' ? 'write' : 'read',
)

function countOf(p: ResearchProject, key: 'papers' | 'drafts' | 'notes'): string {
  const v = p.counts?.[key]
  return Number.isFinite(Number(v)) ? String(Number(v)) : '—'
}

function remember(p: ResearchProject, mode: WorkspaceMode) {
  try {
    localStorage.setItem(LAST_PROJECT_KEY, p.id)
    localStorage.setItem(LAST_WORKSPACE_KEY, mode)
  } catch { /* 隐私模式忽略 */ }
}

function openProject(p: ResearchProject, mode: WorkspaceMode) {
  remember(p, mode)
  router.push(`/research-workspace/${p.id}/${mode}`)
}

async function refresh() {
  loading.value = true
  error.value = ''
  await projectStore.loadList()
  error.value = projectStore.error?.message || ''
  loading.value = false
}

async function createAndOpen(mode: WorkspaceMode) {
  if (creating.value) return
  creating.value = true
  try {
    const created = await projectStore.create({
      name: '未命名项目',
      description: '',
      client_request_id: newIdempotencyKey(),
    })
    if (!created) {
      error.value = projectStore.error?.message || '项目创建失败，请稍后重试'
      return
    }
    remember(created, mode)
    router.push(`/research-workspace/${created.id}/${mode}`)
  } finally {
    creating.value = false
  }
}

function enterWorkspace() {
  if (lastProject.value) openProject(lastProject.value, lastWorkspace.value)
  else router.push('/research-workspace/workspace')
}
function startRead() {
  if (lastProject.value) openProject(lastProject.value, 'read')
  else void createAndOpen('read')
}
function startWrite() {
  if (lastProject.value) openProject(lastProject.value, 'write')
  else void createAndOpen('write')
}
function startAgent() {
  if (lastProject.value) openProject(lastProject.value, 'read')
  else void createAndOpen('read')
}

function fmtTime(iso?: string): string {
  if (!iso) return '刚刚'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '刚刚'
  const diff = Date.now() - d.getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时前`
  const days = Math.floor(h / 24)
  if (days < 30) return `${days} 天前`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

onMounted(refresh)
</script>

<style scoped>
.rl-page { min-height: 100vh; background: var(--rw-app-bg); color: var(--rw-text); }
.rl-main { max-width: 1180px; margin: 0 auto; padding: 42px 28px 64px; }

.rl-hero {
  display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(340px, .92fr);
  gap: 48px; align-items: center; padding: 18px 0 20px;
}
.rl-eyebrow { display: flex; align-items: center; gap: 8px; font-size: 10px; font-weight: 900; letter-spacing: .16em; color: var(--rw-primary); }
.rl-eyebrow span { width: 24px; height: 1px; background: var(--rw-primary); display: inline-block; }
.rl-hero h1 { margin: 16px 0 16px; font-size: clamp(34px, 4vw, 54px); line-height: 1.12; letter-spacing: -.045em; font-weight: 900; color: var(--rw-text); }
.rl-hero h1 em { font-style: normal; color: var(--rw-primary); }
.rl-lead { max-width: 540px; margin: 0; font-size: 14px; line-height: 1.8; color: var(--rw-text-2); }
.rl-cta-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 26px; }
.rl-cta { height: 44px; padding: 0 20px; border: 1px solid var(--rw-border-strong); border-radius: 12px; background: var(--rw-surface); color: var(--rw-text); font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; transition: all .18s ease; }
.rl-cta:hover:not(:disabled) { border-color: var(--rw-primary); color: var(--rw-primary); transform: translateY(-1px); }
.rl-cta--primary { border-color: transparent; background: var(--gradient-brand); color: #fff; box-shadow: 0 12px 28px -14px rgba(79,70,229,.65); }
.rl-cta--primary:hover:not(:disabled) { color: #fff; filter: brightness(1.04); }
.rl-cta:disabled { opacity: .55; cursor: wait; }
.rl-cta span { margin-left: 4px; }
.rl-trust { display: flex; align-items: center; gap: 8px; margin-top: 18px; color: var(--rw-text-3); font-size: 10.5px; }
.rl-trust i { font-style: normal; opacity: .5; }

.rl-paper-wrap { position: relative; min-width: 0; }
.rl-paper-shadow {
  position: absolute; inset: 12px -10px -12px 12px; border-radius: 18px;
  background: linear-gradient(135deg, #e0e7ff, #ecfeff); opacity: .8; transform: rotate(1.2deg);
}
.rl-paper {
  position: relative; padding: 20px 22px 18px; border: 1px solid var(--rw-border);
  border-radius: 18px; background:
    linear-gradient(180deg, rgba(238,242,255,.55), rgba(255,255,255,.98) 34%),
    #fff;
  box-shadow: var(--rw-shadow-pop);
  overflow: hidden;
}
.rl-paper::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background-image: linear-gradient(rgba(79,70,229,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(79,70,229,.035) 1px, transparent 1px);
  background-size: 26px 26px;
  mask-image: linear-gradient(180deg, rgba(0,0,0,.5), transparent 70%);
}
.rl-paper-head { position: relative; z-index: 1; display: flex; align-items: center; gap: 8px; font-size: 9px; font-weight: 900; letter-spacing: .12em; color: var(--rw-text-3); }
.rl-paper-mark { width: 24px; height: 24px; display: grid; place-items: center; border-radius: 7px; background: var(--rw-primary); color: #fff; font: 900 14px Georgia, serif; }
.rl-paper-state { margin-left: auto; font-weight: 700; letter-spacing: 0; color: var(--rw-primary); background: var(--rw-primary-soft); padding: 2px 7px; border-radius: 999px; }
.rl-paper-title { position: relative; z-index: 1; margin: 20px 0 7px; font-size: 22px; line-height: 1.3; font-weight: 900; letter-spacing: -.02em; color: var(--rw-text); }
.rl-paper-desc { position: relative; z-index: 1; margin: 0; font-size: 12px; line-height: 1.7; color: var(--rw-text-2); }
.rl-paper-counts { position: relative; z-index: 1; display: flex; gap: 14px; margin-top: 18px; color: var(--rw-text-3); font-size: 10.5px; }
.rl-paper-counts b { color: var(--rw-text); font-family: var(--rw-font-mono); font-size: 13px; margin-right: 2px; }
.rl-paper-rule { height: 1px; background: var(--rw-border); margin: 17px 0 12px; }
.rl-paper-next { position: relative; z-index: 1; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.rl-paper-label { display: block; font-size: 9px; color: var(--rw-text-3); margin-bottom: 2px; }
.rl-paper-next b { font-size: 11.5px; color: var(--rw-text); }
.rl-paper-next button { height: 30px; padding: 0 12px; border: 0; border-radius: 8px; background: var(--rw-primary); color: #fff; font: inherit; font-size: 11px; font-weight: 800; cursor: pointer; }
.rl-paper-formula { position: relative; z-index: 1; display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 20px; padding-top: 12px; border-top: 1px dashed var(--rw-border); color: var(--rw-text-3); }
.rl-paper-formula span { font-family: var(--rw-font-mono); font-size: 12px; color: var(--rw-primary); opacity: .8; }
.rl-paper-formula i { font-style: normal; font-size: 9.5px; letter-spacing: .12em; }
.rl-paper-steps { position: relative; z-index: 1; display: flex; flex-direction: column; gap: 8px; margin-top: 18px; }
.rl-paper-steps span { display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: var(--rw-text-2); }
.rl-paper-steps b { font-family: var(--rw-font-mono); font-size: 9px; color: var(--rw-primary); }
.rl-paper-create { position: relative; z-index: 1; margin-top: 18px; height: 34px; padding: 0 14px; border: 0; border-radius: 9px; background: var(--rw-primary); color: #fff; font: inherit; font-size: 11.5px; font-weight: 800; cursor: pointer; }

.rl-start { margin-top: 34px; }
.rl-section-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; margin-bottom: 14px; }
.rl-kicker { font-size: 9px; font-weight: 900; letter-spacing: .16em; color: var(--rw-primary); }
.rl-section-head h2 { margin: 4px 0 0; font-size: 22px; font-weight: 900; letter-spacing: -.02em; color: var(--rw-text); }
.rl-section-head p { margin: 0 0 2px; font-size: 11px; color: var(--rw-text-3); }
.rl-paths { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.rl-path {
  position: relative; display: flex; flex-direction: column; align-items: flex-start; min-height: 200px;
  padding: 18px 18px 16px; border: 1px solid var(--rw-border); border-radius: 16px;
  background: var(--rw-surface); text-align: left; cursor: pointer; font: inherit;
  transition: all .2s ease; overflow: hidden;
}
.rl-path:hover:not(:disabled) { transform: translateY(-3px); border-color: var(--rw-primary); box-shadow: var(--rw-shadow-pop); }
.rl-path:disabled { opacity: .55; cursor: wait; }
.rl-path::after { content: ""; position: absolute; right: -30px; top: -30px; width: 100px; height: 100px; border-radius: 50%; background: radial-gradient(circle, rgba(79,70,229,.1), transparent 70%); }
.rl-path-no { position: absolute; top: 16px; right: 17px; font-family: var(--rw-font-mono); font-size: 10px; color: var(--rw-text-3); }
.rl-path-ic { font-size: 22px; margin-bottom: 14px; }
.rl-path-title { font-size: 16px; font-weight: 900; color: var(--rw-text); }
.rl-path-desc { margin-top: 6px; font-size: 11.5px; line-height: 1.65; color: var(--rw-text-2); }
.rl-path-go { margin-top: auto; padding-top: 16px; font-size: 11px; font-weight: 800; color: var(--rw-primary); }
.rl-path-go i { font-style: normal; }
.rl-chain { display: flex; align-items: center; gap: 9px; margin-top: 12px; padding: 10px 14px; border: 1px dashed var(--rw-border-strong); border-radius: 11px; color: var(--rw-text-2); font-size: 11px; }
.rl-chain span { font-weight: 800; color: var(--rw-primary); }
.rl-chain i { font-style: normal; color: var(--rw-text-3); }
.rl-chain em { margin-left: auto; font-style: normal; font-size: 10px; color: var(--rw-text-3); }

.rl-projects { margin-top: 34px; }
.rl-mini-btn { height: 30px; padding: 0 11px; border: 1px solid var(--rw-border-strong); border-radius: 8px; background: var(--rw-surface); color: var(--rw-text-2); font: inherit; font-size: 11px; font-weight: 700; cursor: pointer; }
.rl-mini-btn:hover { border-color: var(--rw-primary); color: var(--rw-primary); }
.rl-project-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.rl-project { padding: 15px 16px 13px; border: 1px solid var(--rw-border); border-radius: 14px; background: var(--rw-surface); transition: all .18s ease; }
.rl-project:hover { border-color: var(--rw-primary); box-shadow: var(--rw-shadow-card); }
.rl-project-top { display: flex; align-items: center; justify-content: space-between; }
.rl-project-ic { width: 28px; height: 28px; display: grid; place-items: center; border-radius: 8px; background: var(--rw-primary-soft); color: var(--rw-primary); font: 900 14px Georgia, serif; }
.rl-project-time { font-size: 9.5px; color: var(--rw-text-3); }
.rl-project h3 { margin: 12px 0 5px; font-size: 13.5px; font-weight: 800; color: var(--rw-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rl-project p { margin: 0; min-height: 34px; font-size: 10.5px; line-height: 1.6; color: var(--rw-text-3); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.rl-project-counts { display: flex; gap: 10px; margin-top: 12px; color: var(--rw-text-3); font-size: 9.5px; }
.rl-project-actions { display: flex; gap: 6px; margin-top: 12px; }
.rl-project-actions button { height: 28px; padding: 0 10px; border: 1px solid var(--rw-border); border-radius: 7px; background: var(--rw-surface); color: var(--rw-text-2); font: inherit; font-size: 10.5px; font-weight: 700; cursor: pointer; }
.rl-project-actions button:hover { border-color: var(--rw-primary); color: var(--rw-primary); }
.rl-state { padding: 26px 20px; border: 1px dashed var(--rw-border-strong); border-radius: 14px; text-align: center; background: rgba(255,255,255,.55); }
.rl-state b { font-size: 13px; color: var(--rw-text); }
.rl-state p { margin: 6px auto 12px; max-width: 460px; font-size: 11px; line-height: 1.6; color: var(--rw-text-3); }
.rl-state button { height: 32px; padding: 0 14px; border: 0; border-radius: 8px; background: var(--rw-primary); color: #fff; font: inherit; font-size: 11px; font-weight: 800; cursor: pointer; }
.rl-state button.ghost { margin-left: 6px; border: 1px solid var(--rw-border); background: var(--rw-surface); color: var(--rw-text-2); }
.rl-state--error { border-color: var(--rw-border); background: var(--rw-danger-soft); }
.rl-project-skeleton { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.rl-project-skeleton span { height: 150px; border-radius: 14px; background: var(--rw-surface-2); animation: rl-pulse 1.4s ease-in-out infinite; }
@keyframes rl-pulse { 50% { opacity: .5; } }

@media (max-width: 960px) {
  .rl-hero { grid-template-columns: 1fr; gap: 28px; }
  .rl-paper-wrap { max-width: 620px; }
  .rl-paths { grid-template-columns: 1fr; }
  .rl-path { min-height: 0; }
  .rl-project-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .rl-main { padding: 26px 16px 48px; }
  .rl-hero h1 { font-size: 32px; }
  .rl-section-head { align-items: flex-start; flex-direction: column; gap: 6px; }
  .rl-project-grid { grid-template-columns: 1fr; }
  .rl-chain em { display: none; }
}
</style>
