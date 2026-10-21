/**
 * 教案十板块框架（SPEC §6.1）：结构性常量——板块顺序、默认时长、可挂例题。
 * 此前与演示数据同文件存放，导致应用代码 import 演示数据文件（M3 grep 审计红线）；
 * 现迁出为共享常量：PrepView（大纲失败回退骨架）与演示服务端（fixture）同源引用。
 */
export interface V3TenBoard {
  id: string
  name: string
  minutes: number
  attachable: boolean
  non_instructional?: boolean
}

export const V3_TEN_BOARDS: V3TenBoard[] = [
  { id: 'bd-context',    name: '课标与学情',     minutes: 2, attachable: false },
  { id: 'bd-objectives', name: '教学目标',       minutes: 0, attachable: false, non_instructional: true },
  { id: 'bd-keypoints',  name: '教学重难点',     minutes: 0, attachable: false, non_instructional: true },
  { id: 'bd-intro',      name: '情境引入',       minutes: 4, attachable: false },
  { id: 'bd-explore',    name: '新知探究',       minutes: 12, attachable: false },
  { id: 'bd-examples',   name: '例题精讲',       minutes: 14, attachable: true },
  { id: 'bd-variation',  name: '变式训练',       minutes: 8, attachable: true },
  { id: 'bd-pitfalls',   name: '易错辨析',       minutes: 5, attachable: false },
  { id: 'bd-summary',    name: '课堂小结与检测', minutes: 3, attachable: false },
  { id: 'bd-homework',   name: '分层作业',       minutes: 2, attachable: true },
]
