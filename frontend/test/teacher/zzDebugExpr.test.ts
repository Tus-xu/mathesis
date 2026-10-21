import { describe, it } from 'vitest'
import { parseLatexExpr, makeFn } from '@/components/mathx/expr'

describe('debug', () => {
  it('dump', () => {
    for (const e of ['\\sin\\pi', 'y=\\arcsin x', 'y=\\cot x', 'y=\\ln x', 'y=\\sinh x', 'y=a\\cdot\\sin(bx+c)+d', 'y=e^{-x}', '\\frac{\\sqrt{3}}{2}', 'y=x^{2}', '\\pi x']) {
      const p = parseLatexExpr(e)
      console.log(JSON.stringify(e), '=>', JSON.stringify(p.js), '| params:', JSON.stringify(p.params), '| err:', p.error || '-')
      console.log('   f(1,a=2,c=0,d=1) =', makeFn(p)(1, { a: 2, c: 0, d: 1 }), '| f(0.5,1,1,0,0) =', makeFn(p)(0.5, { a: 1, b: 1, c: 0, d: 0 }))
    }
  })
})
