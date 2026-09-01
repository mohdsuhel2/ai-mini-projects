import { ImageResponse } from 'next/og'
import { SITE } from '@/lib/site'

export const alt = SITE.title
export const size = { width: 1200, height: 630 }
export const contentType = 'image/png'

/**
 * Generated at build time rather than shipped as a binary, so the card can
 * never drift out of step with the copy it quotes.
 */
export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          background: '#FBFAF9',
          padding: '72px 80px',
          fontFamily: 'sans-serif',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
          <div
            style={{
              width: 64,
              height: 64,
              borderRadius: 18,
              background: 'linear-gradient(135deg, #6E6EE4, #4B4BC4)',
              display: 'flex',
            }}
          />
          <div style={{ fontSize: 34, fontWeight: 600, color: '#17161A', letterSpacing: -0.5 }}>
            Simply Notes
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
          <div
            style={{
              fontSize: 68,
              fontWeight: 600,
              color: '#17161A',
              letterSpacing: -2.4,
              lineHeight: 1.1,
              maxWidth: 900,
              display: 'flex',
            }}
          >
            Plan your day. Remember where it went.
          </div>
          <div style={{ fontSize: 30, color: '#6F6C76', maxWidth: 860, display: 'flex' }}>
            A private, local-first planner and activity tracker. No account, no server, no login.
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ height: 4, width: 120, borderRadius: 2, background: '#5B5BD6' }} />
          <div style={{ fontSize: 24, color: '#9A97A1' }}>notes.noobius.in</div>
        </div>
      </div>
    ),
    size,
  )
}
