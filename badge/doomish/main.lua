-- DOOM-ish: a raycaster FPS drawn with box widgets (the badge has no pixel API).
-- D-pad move/turn, hold B + LEFT/RIGHT to strafe, A shoot, START fps, AUX attract spin, HOME exit.

local B, K = badge.input.BUTTON, badge.input.KIND
local floor, sin, cos, min, max, sqrt = math.floor, math.sin, math.cos, math.min, math.max, math.sqrt

-- Every widget call costs ~1 ms and the redraw scales with area, so the view is a
-- 256x160 window (like Doom's reduced screen size) of 32 centre-anchored columns.
local MW, N, CW, VW, VH, HZ, FOV = 16, 32, 8, 256, 160, 80, 0.66
local map, spawns, sh, camx = {}, {}, {}, {}
local cols, lasth, lastc, zbuf = {}, {}, {}, {}
local imps = {}
local px, py, ang, hp, ammo, kills = 1.5, 1.5, 0, 100, 50, 0
local dirX, dirY, plX, plY = 1, 0, 0, FOV
local state, dirty, last, bob, last_gy = "play", true, 0, 0, 0
local flash_until, hurt_until, led_until = 0, 0, 0
local gun, barrel, flash, hurt, hud_hp, hud_face, hud_info, over, over_l, front
local show_fps, frames, fps_at, attract, spent = false, 0, 0, false, 0

local function solid(x, y)
  return map[floor(y) * MW + floor(x) + 1] ~= 0
end

local function led(r, g, b, now, ms)
  badge.led.set_all(r, g, b)
  badge.led.show()
  led_until = now + ms
end

local function hud()
  hud_hp:set_text(string.format("HP %d", hp))
  hud_face:set_text(hp <= 0 and "X-(" or (hp > 70 and ":-)") or (hp > 35 and ":-|") or ":-(")
  if not show_fps then
    hud_info:set_text(string.format("AMMO %d\nIMPS %d/%d", ammo, kills, #spawns))
  end
end

local function banner(text)
  over_l:set_text(text)
  over:hidden(false)
  over:bring_to_front()
end

local function reset()
  px, py, ang, hp, ammo, kills = 1.5, 1.5, 0, 100, 50, 0
  for i, s in ipairs(spawns) do
    local e = imps[i]
    e.x, e.y, e.hp, e.see, e.next_see, e.next_hit, e.vis = s[1], s[2], 3, false, 0, 0, false
  end
  state, dirty = "play", true
  over:hidden(true)
  hud()
end

local function sees(e)
  local dx, dy = px - e.x, py - e.y
  local d = sqrt(dx * dx + dy * dy)
  if d > 9 then return false end
  local steps = floor(d * 3)
  for i = 1, steps do
    if solid(e.x + dx * i / steps, e.y + dy * i / steps) then return false end
  end
  return true
end

local function farther(a, b) return (a.depth or 0) > (b.depth or 0) end

local function render(now)
  local pxi, pyi = floor(px), floor(py)
  for x = 1, N do
    local cx = camx[x]
    local rdx, rdy = dirX + plX * cx, dirY + plY * cx
    if rdx == 0 then rdx = 1e-6 end
    if rdy == 0 then rdy = 1e-6 end
    local ddx, ddy = 1 / rdx, 1 / rdy
    if ddx < 0 then ddx = -ddx end
    if ddy < 0 then ddy = -ddy end
    local mx, my, sx, sy, stx, sty = pxi, pyi, 0, 0, 1, 1
    if rdx < 0 then stx = -1; sx = (px - mx) * ddx else sx = (mx + 1 - px) * ddx end
    if rdy < 0 then sty = -1; sy = (py - my) * ddy else sy = (my + 1 - py) * ddy end
    local side, t = 0, 0
    for _ = 1, 32 do
      if sx < sy then sx = sx + ddx; mx = mx + stx; side = 0
      else sy = sy + ddy; my = my + sty; side = 1 end
      t = map[my * MW + mx + 1]
      if t ~= 0 then break end
    end
    local perp = side == 0 and sx - ddx or sy - ddy
    if perp < 0.05 then perp = 0.05 end
    zbuf[x] = perp
    local h = min(VH, floor(176 / perp)) & -2
    local c = sh[t * 12 + side * 6 + min(5, floor(perp * 0.6))]
    local w = cols[x]
    if h ~= lasth[x] then
      lasth[x] = h
      w:set_size(CW, h)
    end
    if c ~= lastc[x] then lastc[x] = c; w:set_color(c) end
  end

  local shown = 0
  for _, e in ipairs(imps) do
    local vis = false
    if e.hp > 0 then
      local dx, dy = e.x - px, e.y - py
      local depth = dx * dirX + dy * dirY
      if depth > 0.3 then
        local scr = floor(128 * (1 + (dx * -dirY + dy * dirX) / (FOV * depth)))
        local ci = scr // CW + 1
        if ci >= 1 and ci <= N and depth < zbuf[ci] then
          local s = min(150, floor(136 / depth))
          local w = s * 6 // 10
          local top = HZ - s // 2 + s // 8
          e.body:set_size(w, s)
          e.body:set_pos(scr - w // 2, top)
          e.body:set_color(now < e.flash and 0xffffff or 0x9c2a1c)
          local ey = max(2, s // 8)
          e.eye1:set_size(ey, ey); e.eye1:set_pos(scr - w // 4 - ey // 2, top + s // 5)
          e.eye2:set_size(ey, ey); e.eye2:set_pos(scr + w // 4 - ey // 2, top + s // 5)
          vis, e.scr, e.w, e.depth = true, scr, w, depth
          shown = shown + 1
        end
      end
    end
    if vis ~= e.vis then
      e.vis = vis
      e.body:hidden(not vis); e.eye1:hidden(not vis); e.eye2:hidden(not vis)
    end
  end
  if shown > 1 then
    table.sort(imps, farther)
    for _, e in ipairs(imps) do
      if e.vis then e.body:bring_to_front(); e.eye1:bring_to_front(); e.eye2:bring_to_front() end
    end
    for _, w in ipairs(front) do w:bring_to_front() end
  end
  local gy = 118 + floor(3 * sin(bob))
  if gy ~= last_gy then
    last_gy = gy
    gun:set_pos(110, gy)
    barrel:set_pos(120, gy - 14)
  end
end

local function shoot(now)
  if ammo <= 0 then led(40, 40, 0, now, 80); return end
  ammo = ammo - 1
  flash:hidden(false)
  flash_until, dirty = now + 90, true
  led(255, 220, 120, now, 70)
  local best
  for _, e in ipairs(imps) do
    if e.vis and e.hp > 0 and math.abs(e.scr - 128) < e.w // 2 + 6 and
       (not best or e.depth < best.depth) then
      best = e
    end
  end
  if best then
    best.hp = best.hp - 1
    best.flash = now + 120
    if best.hp <= 0 then
      kills = kills + 1
      if kills == #spawns then
        state = "win"
        banner("RIP AND TEAR: COMPLETE\nall imps evicted\n\nA: again")
        led(0, 255, 60, now, 1500)
      end
    end
  end
  hud()
end

local function think(now, dt)
  for _, e in ipairs(imps) do
    if e.flash > 0 and now >= e.flash then e.flash, dirty = 0, true end
    if e.hp > 0 then
      if now >= e.next_see then e.next_see = now + 400; e.see = sees(e) end
      if e.see then
        local dx, dy = px - e.x, py - e.y
        local d = sqrt(dx * dx + dy * dy)
        if d > 0.8 then
          local step = 0.0012 * dt / d
          local nx, ny = e.x + dx * step, e.y + dy * step
          if not solid(nx, e.y) then e.x = nx end
          if not solid(e.x, ny) then e.y = ny end
          dirty = true
        elseif now >= e.next_hit then
          e.next_hit = now + 700
          hp = hp - 9
          hurt:hidden(false)
          hurt_until, dirty = now + 130, true
          led(255, 0, 0, now, 200)
          if hp <= 0 then
            hp, state = 0, "dead"
            banner("YOU DIED\nthe imps send their regards\n\nA: retry")
          end
          hud()
        end
      end
    end
  end
end

-- One-shot world/UI construction. These are dropped after on_enter so their code is
-- garbage-collected. (A separate require()d module was tried: compiling a second
-- chunk from inside the app trips the firmware's Lua stack guard.)
local world, scene, build_hud, box, text

-- 16x16, row-major. 1-4 wall types, P player start, E imp spawn.
-- table.concat, not a chain of "..": the parser recurses once per ".." and 16 of
-- them ate enough C stack to trip the firmware's "Lua stack safety limit".
local LEVEL = table.concat({
  "1111111111111111", "1P....1........1", "1.....1...E....1", "1..2..1........1",
  "1..2......33.331", "1..2......3...31", "1.........3.E.31", "1111.111..3...31",
  "1......1..333331", "1..E...1.......1", "1......1..44...1", "1......4..44.E.1",
  "1..22..4.......1", "1..22......E...1", "1..............1", "1111111111111111",
})
local BASE = {0x8a8a8a, 0xa8402c, 0x2f8070, 0xc87820}

-- One reused style table and positional factories: the table-form factories
-- leave ~300 B of garbage per widget, and with ~70 widgets that ran the whole
-- system out of RAM before Lua's collector ever woke up.
local ST = {bg_color = 0, bg_opa = 255, radius = 0, border_width = 0, pad_all = 0}
local LS = {text_font = 14, text_color = 0xffffff}
local made = 0

function box(parent, x, y, w, h, color, hidden, opa, radius)
  local o = badge.ui.box(parent, w, h)
  ST.bg_color, ST.bg_opa, ST.radius = color, opa or 255, radius or 0
  o:style(ST)
  o:set_pos(x, y)
  if hidden then o:hidden(true) end
  made = made + 1
  if made % 12 == 0 then collectgarbage() end
  return o
end

function text(parent, x, y, font, color)
  local o = badge.ui.label(parent, "")
  LS.text_font, LS.text_color = font, color
  o:style(LS)
  o:set_pos(x, y)
  return o
end

function world(G)
  local MW, map, spawns, sh = G.MW, G.map, G.spawns, G.sh
  for i = 1, MW * MW do
    local ch = LEVEL:sub(i, i)
    map[i] = tonumber(ch) or 0
    if ch == "E" then
      spawns[#spawns + 1] = {(i - 1) % MW + 0.5, (i - 1) // MW + 0.5}
    end
  end
  for t = 1, 4 do
    local c = BASE[t]
    for k = 0, 11 do
      local f = (k < 6 and 1 or 0.72) * (1 - (k % 6) * 0.14)
      sh[t * 12 + k] = (floor((c >> 16) * f) << 16) |
        (floor(((c >> 8) & 255) * f) << 8) | floor((c & 255) * f)
    end
  end
  for x = 1, G.N do
    G.camx[x] = 2 * (x - 0.5) / G.N - 1
    G.lasth[x], G.lastc[x], G.zbuf[x] = -1, -1, 99
  end
end

function scene(G)
  local root, VW, VH, HZ, CW = G.root, G.VW, G.VH, G.HZ, G.CW
  box(root, 0, 0, 320, 240, 0x241a12)
  local view = box(root, 32, 20, VW, VH, 0x2a2a30)
  box(view, 0, HZ, VW, VH - HZ, 0x4a3a2a)
  for x = 1, G.N do
    local c = box(view, 0, 0, CW, 2, 0)
    c:align("left_mid", (x - 1) * CW, 0)
    G.cols[x] = c
  end
  for i = 1, #G.spawns do
    local e = {flash = 0}
    e.body = box(view, 0, 0, 8, 8, 0x9c2a1c, true, 255, 6)
    e.eye1 = box(view, 0, 0, 2, 2, 0xffe040, true)
    e.eye2 = box(view, 0, 0, 2, 2, 0xffe040, true)
    G.imps[i] = e
  end
  G.barrel = box(view, 120, 104, 16, 20, 0x303034)
  G.gun = box(view, 110, 118, 36, 44, 0x55555c)
  G.flash = box(view, 114, 84, 28, 24, 0xffd040, true, 255, 12)
  local cross = text(view, 124, 70, 16, 0xffffff)
  cross:set_text("+")
  G.hurt = box(view, 0, 0, VW, VH, 0xff0000, true, 90)
  G.front = {G.barrel, G.gun, G.flash, cross, G.hurt}
end

function build_hud(G)
  local bar = box(G.root, 0, 200, 320, 40, 0x38383c)
  G.hud_hp = text(bar, 10, 6, 24, 0xff5040)
  G.hud_face = text(bar, 140, 6, 24, 0xffd080)
  G.hud_info = text(bar, 222, 3, 14, 0xd0d0d0)
  G.over = box(G.root, 30, 40, 260, 120, 0x100000, true, 220, 8)
  local l = text(G.over, 0, 0, 18, 0xff5040)
  l:set_size(240, 100)
  l:style({text_align = "center"})
  l:align("center", 0, 0)
  G.over_l = l
end

function on_enter(root)
  local G = {root = root, MW = MW, N = N, CW = CW, VW = VW, VH = VH, HZ = HZ,
    map = map, spawns = spawns, sh = sh, camx = camx, cols = cols,
    lasth = lasth, lastc = lastc, zbuf = zbuf, imps = imps}
  -- Compiling this file leaves ~15 KB of garbage. With it uncollected the system has
  -- <14 KB free and the firmware refuses the next call ("Lua stack safety limit").
  collectgarbage()
  world(G)
  collectgarbage()
  scene(G)
  build_hud(G)
  world, scene, build_hud, box, text = nil, nil, nil, nil, nil
  gun, barrel, flash, hurt = G.gun, G.barrel, G.flash, G.hurt
  hud_hp, hud_face, hud_info, over, over_l, front = G.hud_hp, G.hud_face, G.hud_info, G.over, G.over_l, G.front
  collectgarbage()

  reset()
  last = badge.sys.ms()
  fps_at = last
end

function on_tick()
  local now = badge.sys.ms()
  local dt = min(100, now - last)
  last = now
  if led_until > 0 and now >= led_until then
    led_until = 0
    badge.led.clear(); badge.led.show()
  end
  if flash_until > 0 and now >= flash_until then flash_until = 0; flash:hidden(true) end
  if hurt_until > 0 and now >= hurt_until then hurt_until = 0; hurt:hidden(true) end
  badge.sys.gc_step()
  if state ~= "play" then return end

  local is_down = badge.input.is_down
  local fwd = (is_down(B.UP) and 1 or 0) - (is_down(B.DOWN) and 1 or 0)
  local turn = (is_down(B.RIGHT) and 1 or 0) - (is_down(B.LEFT) and 1 or 0)
  if attract and turn == 0 then turn = 0.35 end
  local mvx, mvy = dirX * fwd, dirY * fwd
  if turn ~= 0 then
    if is_down(B.B) then
      mvx, mvy = mvx - dirY * turn, mvy + dirX * turn
    else
      ang = ang + turn * 0.0026 * dt
      dirX, dirY = cos(ang), sin(ang)
      plX, plY = -dirY * FOV, dirX * FOV
      dirty = true
    end
  end
  if mvx ~= 0 or mvy ~= 0 then
    local sp = 0.0030 * dt
    local nx, ny = px + mvx * sp, py + mvy * sp
    if not solid(nx + (mvx > 0 and 0.2 or -0.2), py) then px = nx end
    if not solid(px, ny + (mvy > 0 and 0.2 or -0.2)) then py = ny end
    bob = bob + dt * 0.012
    dirty = true
  end

  think(now, dt)
  if dirty then
    dirty = false
    render(now)
    frames = frames + 1
    spent = spent + badge.sys.ms() - now
  end
  if show_fps and now - fps_at >= 1000 then
    hud_info:set_text(string.format("%d fps\n%d ms lua", frames * 1000 // (now - fps_at), spent // max(1, frames)))
    frames, fps_at, spent = 0, now, 0
  end
end

function on_button(button, kind)
  if kind ~= K.PRESSED then return end
  local now = badge.sys.ms()
  if button == B.A then
    if state == "play" then shoot(now) else reset() end
  elseif button == B.AUX1 then
    attract = not attract  -- arcade attract mode: slow camera spin
  elseif button == B.START then
    show_fps = not show_fps
    frames, fps_at = 0, now
    hud()
  end
end

function on_exit()
  badge.led.clear()
  badge.led.show()
end

collectgarbage()
