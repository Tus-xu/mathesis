<template>
  <div class="conv-sidebar">
    <!-- 搜索框（M2 §4.3；防抖后 emit search，由 useChat 走 q 参数+客户端兜底过滤） -->
    <div class="cs-search">
      <UiIcon name="search" :size="13" class="cs-search-icon" />
      <input
        v-model="query" class="cs-search-input" type="text" placeholder="搜索对话…"
        @input="onQueryInput" @keydown.esc="clearQuery"
      />
      <button v-if="query" class="cs-search-clear" title="清空" @click="clearQuery">
        <UiIcon name="close" :size="12" />
      </button>
    </div>

    <!-- 骨架屏（首次加载） -->
    <div v-if="loading && !conversations.length" class="cs-skel-list">
      <div v-for="i in 6" :key="i" class="cs-skel">
        <div class="cs-skel-line w70"></div>
        <div class="cs-skel-line w40"></div>
      </div>
    </div>

    <div v-else-if="!conversations.length" class="empty-state cs-empty">
      <div class="es-icon">🗨️</div>
      <div class="es-text">{{ query ? '没有匹配的对话' : '暂无会话' }}<br />{{ query ? '换个关键词试试' : '发起一个新对话吧' }}</div>
    </div>

    <!-- 分组列表（置顶组 + 今天/7天内/30天内/更早；无限滚动） -->
    <div v-else ref="listRef" class="cs-list" @scroll="onScroll">
      <template v-for="g in groups" :key="g.key">
        <div v-if="g.items.length" class="cs-group">
          <div class="cs-group-title">
            <UiIcon v-if="g.key === 'pinned'" name="pin" :size="11" class="cs-group-pin-icon" />{{ g.label }}
          </div>
          <div
            v-for="c in g.items"
            :key="c.id"
            class="cs-item"
            :class="{ active: c.id === activeId }"
            @click="$emit('select', c.id)"
          >
            <div class="cs-item-main">
              <!-- 重命名内联编辑 -->
              <input
                v-if="renamingId === c.id"
                ref="renameInputRef"
                v-model="renameDraft"
                class="cs-rename-input"
                maxlength="60"
                @click.stop
                @keydown.enter.prevent="submitRename(c)"
                @keydown.esc.prevent="cancelRename"
                @blur="submitRename(c)"
              />
              <template v-else>
                <div class="cs-item-title" @dblclick.stop="startRename(c)">
                  <UiIcon v-if="c.pinned" name="pin" :size="11" class="cs-title-pin" />{{ c.title || '新对话' }}
                </div>
                <div class="cs-item-time">{{ fmtTime(c.updatedAt || c.createdAt) }}</div>
              </template>
            </div>

            <!-- hover 菜单按钮（菜单本体 Teleport 到 body，避免被 .cs-list 的 overflow 裁剪） -->
            <div v-if="renamingId !== c.id" class="cs-menu-wrap" @click.stop>
              <button class="cs-menu-btn" title="更多操作" @click="toggleMenu(c, $event)">
                <UiIcon name="more" :size="15" />
              </button>
            </div>
          </div>
        </div>
      </template>

      <!-- 无限滚动：加载更多 -->
      <div v-if="loading && conversations.length" class="cs-more-loading"><span class="spinner"></span> 加载更多…</div>
      <div v-else-if="!hasMore && conversations.length > 10" class="cs-end">— 到底啦 —</div>
    </div>

    <!-- 行内菜单：fixed 定位 + 空间不足时向上展开（root cause: absolute 菜单会被 .cs-list overflow-y:auto 裁剪，底部会话的「删除」点不到） -->
    <Teleport to="body">
      <div v-if="menuOpenId" class="cs-menu cs-menu-pop glass-card" :style="menuStyle" @click.stop>
        <button class="cs-menu-item" @click="onPinClick(menuConv)">
          <UiIcon :name="menuConv?.pinned ? 'pin-off' : 'pin'" :size="13" />{{ menuConv?.pinned ? '取消置顶' : '置顶' }}
        </button>
        <button class="cs-menu-item" @click="startRename(menuConv)">
          <UiIcon name="edit" :size="13" />重命名
        </button>
        <button class="cs-menu-item danger" @click="onRemoveClick(menuConv)">
          <UiIcon name="trash" :size="13" />删除
        </button>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import UiIcon from '@/components/common/UiIcon.vue'
import { CONV_GROUPS, convGroupKey, fmtTime } from './messageModel'

const props = defineProps({
  conversations: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  hasMore: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'remove', 'rename', 'togglePin', 'search', 'loadMore'])

/* ===== 搜索（300ms 防抖） ===== */
const query = ref('')
let searchTimer = null
function onQueryInput() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => emit('search', query.value.trim()), 300)
}
function clearQuery() {
  query.value = ''
  emit('search', '')
}

/* ===== 分组：置顶组优先，其余按时间分组；各组内 updatedAt desc ===== */
const groups = computed(() => {
  const sorted = [...props.conversations].sort((a, b) => {
    if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1
    return String(b.updatedAt || '').localeCompare(String(a.updatedAt || ''))
  })
  const pinned = sorted.filter((c) => c.pinned)
  const rest = sorted.filter((c) => !c.pinned)
  const buckets = CONV_GROUPS.map((g) => ({ ...g, items: [] }))
  for (const c of rest) {
    const key = convGroupKey(c.updatedAt || c.createdAt)
    buckets.find((g) => g.key === key)?.items.push(c)
  }
  return [{ key: 'pinned', label: '置顶', items: pinned }, ...buckets]
})

/* ===== 无限滚动 ===== */
const listRef = ref(null)
function onScroll() {
  menuOpenId.value = '' // fixed 菜单不跟随滚动，滚动即关闭
  const el = listRef.value
  if (!el || !props.hasMore || props.loading) return
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) emit('loadMore')
}

/* ===== 行内菜单（Teleport 到 body，fixed 定位；空间不足向上展开） ===== */
const MENU_W = 108
const MENU_H = 112 // 3 项 + padding 的估算高
const menuOpenId = ref('')
const menuConv = ref(null)
const menuStyle = ref({})
function toggleMenu(c, e) {
  if (menuOpenId.value === c.id) {
    menuOpenId.value = ''
    return
  }
  const r = e?.currentTarget?.getBoundingClientRect?.()
  if (r) {
    const openUp = r.bottom + 6 + MENU_H > window.innerHeight && r.top - 6 - MENU_H > 0
    menuStyle.value = {
      top: `${openUp ? Math.max(8, r.top - 6 - MENU_H) : r.bottom + 6}px`,
      left: `${Math.max(8, r.right - MENU_W)}px`,
    }
  }
  menuConv.value = c
  menuOpenId.value = c.id
}
function closeMenuOnOutside(e) {
  if (!e.target.closest?.('.cs-menu-wrap') && !e.target.closest?.('.cs-menu-pop')) menuOpenId.value = ''
}
onMounted(() => document.addEventListener('click', closeMenuOnOutside))
onBeforeUnmount(() => {
  document.removeEventListener('click', closeMenuOnOutside)
  clearTimeout(searchTimer)
})

function onPinClick(c) {
  menuOpenId.value = ''
  emit('togglePin', c)
}
function onRemoveClick(c) {
  menuOpenId.value = ''
  emit('remove', c.id)
}

/* ===== 重命名（菜单或双击触发，内联编辑） ===== */
const renamingId = ref('')
const renameDraft = ref('')
const renameInputRef = ref(null)
function startRename(c) {
  menuOpenId.value = ''
  renamingId.value = c.id
  renameDraft.value = c.title || ''
  nextTick(() => {
    const el = Array.isArray(renameInputRef.value) ? renameInputRef.value[0] : renameInputRef.value
    el?.focus()
    el?.select()
  })
}
function cancelRename() {
  renamingId.value = ''
}
function submitRename(c) {
  if (renamingId.value !== c.id) return
  const t = renameDraft.value.trim()
  renamingId.value = ''
  if (t && t !== (c.title || '')) emit('rename', c.id, t)
}
</script>

<style scoped>
.conv-sidebar { display: flex; flex-direction: column; height: 100%; min-height: 0; }

/* 搜索 */
.cs-search {
  position: relative; display: flex; align-items: center; margin-bottom: var(--space-2); flex-shrink: 0;
}
.cs-search-icon { position: absolute; left: 8px; color: var(--text-muted); pointer-events: none; }
.cs-search-input {
  width: 100%; height: 30px; padding: 0 26px 0 26px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  font-size: var(--text-xs); font-family: var(--font); background: var(--bg-subtle);
  color: var(--text-primary); outline: none; transition: all var(--transition-fast);
}
.cs-search-input:focus { border-color: var(--primary); background: var(--bg-white); box-shadow: 0 0 0 3px var(--primary-subtle); }
.cs-search-input::placeholder { color: var(--text-muted); }
.cs-search-clear {
  position: absolute; right: 5px; width: 20px; height: 20px; border: none; background: none;
  color: var(--text-muted); cursor: pointer; border-radius: var(--radius-sm);
  display: inline-flex; align-items: center; justify-content: center;
}
.cs-search-clear:hover { color: var(--text-primary); background: var(--bg-muted); }

/* 骨架屏 */
.cs-skel-list { display: flex; flex-direction: column; gap: var(--space-3); padding: var(--space-1) 2px; }
.cs-skel { display: flex; flex-direction: column; gap: 6px; }
.cs-skel-line {
  height: 11px; border-radius: var(--radius-sm);
  background: linear-gradient(90deg, var(--bg-muted) 25%, var(--bg-subtle) 50%, var(--bg-muted) 75%);
  background-size: 200% 100%; animation: cs-shimmer 1.3s infinite;
}
.cs-skel-line.w70 { width: 72%; }
.cs-skel-line.w40 { width: 42%; }
@keyframes cs-shimmer { to { background-position: -200% 0; } }

.cs-empty { padding: 32px 8px; }
.cs-list { display: flex; flex-direction: column; gap: 2px; overflow-y: auto; min-height: 0; }

.cs-group { margin-bottom: var(--space-2); }
.cs-group-title {
  display: flex; align-items: center; gap: 4px;
  font-size: 11px; font-weight: var(--font-semibold); color: var(--text-muted);
  letter-spacing: 0.04em; margin: var(--space-1) 2px; user-select: none;
}
.cs-group-pin-icon { color: var(--primary); }

.cs-item {
  position: relative; display: flex; align-items: center; gap: 4px; padding: 8px 8px;
  border-radius: var(--radius-md); cursor: pointer; transition: background 0.15s;
}
.cs-item:hover { background: rgba(138, 90, 86, 0.08); }
.cs-item.active { background: rgba(138, 90, 86, 0.14); }
.cs-item-main { flex: 1; min-width: 0; }
.cs-item-title {
  display: flex; align-items: center; gap: 4px;
  font-size: 13px; color: var(--text-primary); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.cs-title-pin { color: var(--primary); flex-shrink: 0; }
.cs-item.active .cs-item-title { color: var(--primary); font-weight: 600; }
.cs-item-time { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

/* hover 菜单按钮：桌面 hover 显现，触屏常显 */
.cs-menu-wrap { position: relative; flex-shrink: 0; }
.cs-menu-btn {
  border: none; background: none; cursor: pointer; color: var(--text-muted);
  width: 24px; height: 24px; border-radius: var(--radius-sm); opacity: 0;
  display: inline-flex; align-items: center; justify-content: center;
  transition: all var(--transition-fast);
}
.cs-item:hover .cs-menu-btn, .cs-menu-btn:focus-visible { opacity: 1; }
.cs-menu-btn:hover { background: rgba(138, 90, 86, 0.12); color: var(--primary); }
@media (hover: none) { .cs-menu-btn { opacity: 0.7; } }

.cs-menu {
  position: fixed; z-index: 1000; min-width: 108px;
  padding: 4px; display: flex; flex-direction: column; gap: 1px;
  box-shadow: var(--shadow-md);
}
.cs-menu-item {
  display: flex; align-items: center; gap: var(--space-2);
  border: none; background: none; cursor: pointer; text-align: left;
  padding: 6px var(--space-2); border-radius: var(--radius-sm);
  font-size: var(--text-xs); font-family: var(--font); color: var(--text-secondary);
  transition: all var(--transition-fast); white-space: nowrap;
}
.cs-menu-item:hover { background: var(--bg-subtle); color: var(--text-primary); }
.cs-menu-item.danger:hover { background: var(--error-subtle); color: var(--error); }

/* 重命名输入框 */
.cs-rename-input {
  width: 100%; height: 26px; padding: 0 var(--space-2);
  border: 1px solid var(--primary); border-radius: var(--radius-sm); outline: none;
  font-size: 13px; font-family: var(--font); color: var(--text-primary);
  background: var(--bg-white); box-shadow: 0 0 0 3px var(--primary-subtle);
}

.cs-more-loading { display: flex; align-items: center; justify-content: center; gap: 6px; color: var(--text-muted); font-size: 11px; padding: var(--space-2) 0; }
.cs-end { text-align: center; font-size: 11px; color: var(--text-disabled); padding: var(--space-2) 0; }
</style>
