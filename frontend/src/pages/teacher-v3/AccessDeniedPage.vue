<template>
  <div class="tv3-denied">
    <div class="tv3-denied__card">
      <div class="tv3-denied__icon">🔐</div>
      <h1 class="tv3-denied__title">当前账号无教师权限</h1>
      <p class="tv3-denied__desc">
        教师教学工作台仅对<strong>教师身份</strong>开放。你当前登录的账号不具备教师角色，
        因此无法访问备课、课件、题库等教学工作功能。
      </p>
      <p class="tv3-denied__hint">如需教师权限，请联系学校管理员完成身份审核，或切换教师账号后重新登录。</p>
      <div class="tv3-denied__actions">
        <button class="tv3-denied__btn tv3-denied__btn--primary" @click="goHome">返回我的工作台</button>
        <button class="tv3-denied__btn" @click="relogin">重新登录</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 40301 role_denied 统一提示页（A0 M1-2）：教师端 API 返回角色不足时落地此处，
 * 给出可理解的解释与出路，绝不白屏。路由 meta 不带 teacherV3/teacher
 * （router.test.ts 契约：teacherV3 工作区恰好 10 条；legacy 检查不得误伤）。
 */
import { useRouter } from 'vue-router'
import { clearAccessToken } from '@/api/authSession'

const router = useRouter()

function goHome() {
  router.push('/').catch(() => {})
}

function relogin() {
  clearAccessToken()
  router.push({ path: '/login', query: { redirect: '/teacher-v3/today' } }).catch(() => {})
}
</script>

<style scoped>
.tv3-denied {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: linear-gradient(160deg, #f5f7fa 0%, #eef2f7 100%);
  padding: 24px;
}
.tv3-denied__card {
  max-width: 460px;
  background: #fff;
  border: 1px solid #e5e9f0;
  border-radius: 16px;
  padding: 40px 36px;
  text-align: center;
  box-shadow: 0 8px 32px rgba(79, 70, 229, 0.08);
}
.tv3-denied__icon { font-size: 44px; margin-bottom: 14px; }
.tv3-denied__title {
  margin: 0 0 12px;
  font-size: 19px;
  font-weight: 700;
  color: #1a2332;
}
.tv3-denied__desc {
  margin: 0 0 8px;
  font-size: 13.5px;
  line-height: 1.7;
  color: #4a5568;
}
.tv3-denied__hint {
  margin: 0 0 22px;
  font-size: 12.5px;
  line-height: 1.6;
  color: #8899aa;
}
.tv3-denied__actions {
  display: flex;
  justify-content: center;
  gap: 10px;
}
.tv3-denied__btn {
  height: 36px;
  padding: 0 16px;
  border-radius: 8px;
  border: 1px solid #e5e9f0;
  background: #fff;
  color: #4a5568;
  font-size: 13px;
  cursor: pointer;
  transition: all .15s ease;
}
.tv3-denied__btn:hover { border-color: #a5b4fc; color: #4f46e5; }
.tv3-denied__btn--primary {
  background: linear-gradient(135deg, #4f46e5, #6366f1);
  border-color: #4f46e5;
  color: #fff;
  font-weight: 600;
}
.tv3-denied__btn--primary:hover { filter: brightness(1.08); color: #fff; }
</style>
