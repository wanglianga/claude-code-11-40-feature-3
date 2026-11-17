import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from './api'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
    { path: '/', name: 'overview', component: () => import('./views/OverviewView.vue') },
    { path: '/units/:id(\\d+)', name: 'unit', component: () => import('./views/UnitDetailView.vue') },
    { path: '/compare', name: 'compare', component: () => import('./views/CompareView.vue') },
    { path: '/records', name: 'records', component: () => import('./views/RecordsView.vue') },
    { path: '/rulebooks', name: 'rulebooks', component: () => import('./views/RulebooksView.vue') },
    { path: '/conflicts', name: 'conflicts', component: () => import('./views/ConflictsView.vue') },
    { path: '/trends', name: 'trends', component: () => import('./views/TrendsView.vue') },
    { path: '/restoration', name: 'restoration', component: () => import('./views/RestorationView.vue') },
    { path: '/trace', name: 'trace', component: () => import('./views/TraceView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.public && !getToken()) {
    return { name: 'login', query: { next: to.fullPath } }
  }
  if (to.name === 'login' && getToken()) {
    return { name: 'overview' }
  }
  return true
})

export default router
