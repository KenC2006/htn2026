-- Snitch Badge: a name tag that tells on you.
-- A gossip   B rap sheet   START debug numbers   HOME exit (saves the tally)

local B, K = badge.input.BUTTON, badge.input.KIND

-- Tuning, in milli-g of wobble around 1 g. START shows the live numbers.
local STILL_MG, FIDGET_MG, WALK_MG = 35, 110, 380
local FALL_MG, FALL_MS = 400, 90
local SWITCH_MS, ROTATE_MS = 1200, 6500

local QUIPS = {
  calm = {
    "Currently behaving. Suspicious.",
    "@ looks busy. @ is not busy.",
    "Nothing to report. Yet.",
  },
  still = {
    "@ is very still. Thinking? Unlikely.",
    "No movement. Reading docs or asleep.",
    "@ is staring at a stack trace.",
  },
  table = {
    "@ left me on a table. Unsupervised.",
    "I am a coaster now.",
    "Abandoned. Tell @ I said hi.",
  },
  facedown = {
    "Face down. @ is hiding me. Rude.",
    "I can't see. Is the demo over?",
  },
  upside = {
    "@ is wearing me upside down. Classic.",
    "Everything is upside down. Like the repo.",
  },
  sideways = {
    "Sideways. Is @ okay?",
    "@ is horizontal. Draw your own conclusions.",
  },
  fidget = {
    "@ is fidgeting. It does not compile.",
    "Nervous energy detected. Demo soon?",
    "Leg bouncing detected. Classic.",
  },
  walk = {
    "@ is walking. Away from the code.",
    "Pacing. It's a bug. Definitely a bug.",
    "On the move. Probably snacks.",
  },
  run = {
    "SPRINTING. Free food was announced.",
    "@ is running. Deadline or pizza?",
  },
  shake = {
    "Stop shaking me. I bruise easily.",
    "I am not a Polaroid.",
    "Shaking me won't fix the build.",
  },
  dizzy = {
    "I'm gonna be sick...",
    "blegh. please. stop.",
  },
  poke = {
    "Stop poking me.",
    "I felt that.",
    "Poke me again and I talk.",
  },
  gossip = {
    "@ said 'works on my machine' twice today.",
    "@ googled 'what is a blockchain' at 2am.",
    "Every commit message @ writes is 'fix'.",
    "@ practiced this pitch in a bathroom mirror.",
    "@ only came to this booth for stickers.",
    "@ called a for-loop 'AI' earlier.",
    "@ has 47 tabs open. 40 are Stack Overflow.",
  },
}

local first = "My human"
local bg, head, hello, sub, name_l, status, stat, foot, hint
local scream_bg, scream
local have_accel = true
local fast, slow, base = 0, 0, nil
local mood, cand, cand_since = "calm", "calm", 0
local still_since, low_since = 0, nil
local falling, fall_start = false, 0
local override_until, override_mood = 0, nil
local next_rotate, next_led, next_dbg = 0, 0, 0
local shake_times = {}
local debug_on = false
local drops, shakes, pokes, nap_best = 0, 0, 0, 0
local met, sponsors = 0, 0
local dirty = false
local last_text = ""

local function pick(list)
  return list[badge.sys.random(#list) + 1]
end

local function say(text)
  text = (text:gsub("@", first))
  if text ~= last_text then
    last_text = text
    status:set_text(text)
  end
end

local function show_tally()
  if debug_on then return end
  stat:set_text(string.format("drops %d   shakes %d   pokes %d", drops, shakes, pokes))
end

local function paint(body, ink)
  bg:set_color(body)
  status:set_color(ink)
end

-- Text + colours for the ambient mood (no override active).
local function describe(now)
  local idle = (now - still_since) // 1000
  if mood == "still" or mood == "table" then
    if idle // 60 > nap_best then nap_best = idle // 60; dirty = true end
    if idle >= 600 then
      say(string.format("@ has not moved in %d min. Send snacks.", idle // 60)); return
    elseif idle >= 120 then
      say("Asleep. Do not let @ tell you otherwise."); return
    elseif idle >= 30 and mood == "still" then
      say(string.format("@ has not moved in %d sec. Poke to check.", idle)); return
    end
  end
  say(pick(QUIPS[mood]))
end

local function set_mood(m, now)
  mood = m
  if m == "run" then paint(0xffe2c4, 0xa03000)
  elseif m == "facedown" then paint(0x303030, 0xdddddd)
  elseif m == "walk" or m == "fidget" then paint(0xfff6d8, 0x553300)
  else paint(0xffffff, 0x222222) end
  describe(now)
  next_rotate = now + ROTATE_MS
end

local function hold(kind, text, ms, now)
  override_mood, override_until = kind, now + ms
  if kind == "dizzy" then paint(0xd4f2b8, 0x2c5a10)
  elseif kind == "landed" then paint(0xffd0d0, 0x990000)
  elseif kind == "gossip" then paint(0xeadcff, 0x4a1a8a)
  else paint(0xffffff, 0x222222) end
  say(text)
end

local function gossip(now)
  local roll = badge.sys.random(#QUIPS.gossip + 4)
  local text
  if roll == 0 then
    text = string.format("@ has met %d humans. %d were sponsors.", met, sponsors)
  elseif roll == 1 then
    text = string.format("You'd be contact #%d. Don't feel special.", met + 1)
  elseif roll == 2 then
    text = string.format("@ has dropped me %d times. I keep count.", drops)
  elseif roll == 3 then
    text = string.format("Awake %d min. @ promised me sleep.", badge.sys.uptime() // 60)
  else
    text = QUIPS.gossip[roll - 3]
  end
  hold("gossip", text, 6000, now)
end

local function rap_sheet(now)
  hold("gossip", string.format(
    "RAP SHEET\nDrops %d  Shakes %d  Pokes %d\nLongest nap: %d min\nHumans met: %d (%d sponsors)",
    drops, shakes, pokes, nap_best, met, sponsors), 9000, now)
end

local function classify(now)
  local o = badge.sensor.orientation()
  if slow < STILL_MG then
    if o == "flat_down" then return "facedown" end
    if now - still_since > 8000 then
      if o == "flat_up" then return "table" end
      if o == "top_edge" then return "upside" end
      if o == "left_edge" or o == "right_edge" then return "sideways" end
      return "still"
    end
    return "calm"
  end
  still_since = now
  if o == "top_edge" and slow < FIDGET_MG then return "upside" end
  if slow < FIDGET_MG then return "fidget" end
  if slow < WALK_MG then return "walk" end
  return "run"
end

local function leds(now)
  local m = override_mood or mood
  local wave = (now % 2400) / 1200
  if wave > 1 then wave = 2 - wave end
  badge.led.clear()
  if falling then
    local v = (now // 60) % 2 == 0 and 255 or 0
    badge.led.set_all(v, 0, 0)
  elseif m == "landed" then
    badge.led.set_all(math.floor(40 + 160 * wave), 0, 0)
  elseif m == "dizzy" then
    local side = (now // 180) % 2 == 0 and {1, 6, 5} or {2, 3, 4}
    for i = 1, 3 do badge.led.set(side[i], 40, 200, 0) end
  elseif m == "gossip" or m == "poke" then
    local v = math.floor(60 + 150 * wave)
    badge.led.set_all(v, 0, v)
  elseif m == "run" then
    badge.led.set((now // 70) % 6 + 1, 255, 90, 0)
  elseif m == "walk" or m == "fidget" then
    badge.led.set((now // 220) % 6 + 1, 180, 110, 0)
  elseif m == "table" or (m == "still" and now - still_since > 120000) then
    badge.led.set_all(0, 0, math.floor(10 + 50 * wave))
  elseif m ~= "facedown" then
    badge.led.set_all(0, math.floor(20 + 90 * wave), 0)
  end
  badge.led.show()
end

function on_enter(root)
  local n = badge.me.name()
  if n and #n > 0 then first = n:match("^(%S+)") or n end
  first = (first:gsub("%%", ""))
  drops = badge.store.get_int("drops", 0)
  shakes = badge.store.get_int("shakes", 0)
  pokes = badge.store.get_int("pokes", 0)
  nap_best = badge.store.get_int("nap", 0)
  met = math.min(badge.contacts.count() or 0, 200)
  for i = 1, met do
    local c = badge.contacts.get(i)
    if c and (c.role == 2 or c.role == "sponsor") then sponsors = sponsors + 1 end
  end

  bg = badge.ui.box{parent = root, x = 0, y = 0, w = 320, h = 240,
    bg_color = 0xffffff, radius = 0, border_width = 0}
  head = badge.ui.box{parent = root, x = 0, y = 0, w = 320, h = 54,
    bg_color = 0xd62828, radius = 0, border_width = 0}
  hello = badge.ui.label(root, "HELLO")
  hello:style({text_font = 24, text_color = 0xffffff})
  hello:align("top_mid", 0, 3)
  sub = badge.ui.label(root, "my name is")
  sub:style({text_font = 14, text_color = 0xffffff})
  sub:align("top_mid", 0, 32)
  name_l = badge.ui.label(root, n or "Unprovisioned")
  name_l:style({text_font = 24, text_color = 0x111111})
  name_l:align("top_mid", 0, 62)
  status = badge.ui.label{parent = root, text = "", w = 300,
    text_font = 18, text_align = "center", text_color = 0x222222}
  status:align("top_mid", 0, 98)
  stat = badge.ui.label(root, "")
  stat:style({text_font = 14, text_color = 0x777777})
  stat:align("top_mid", 0, 194)
  foot = badge.ui.box{parent = root, x = 0, y = 216, w = 320, h = 24,
    bg_color = 0xd62828, radius = 0, border_width = 0}
  hint = badge.ui.label(root, "A gossip   B rap sheet   HOME exit")
  hint:style({text_font = 14, text_color = 0xffffff})
  hint:align("bottom_mid", 0, -4)

  scream_bg = badge.ui.box{parent = root, x = 0, y = 0, w = 320, h = 240,
    bg_color = 0xd00000, radius = 0, border_width = 0, hidden = true}
  scream = badge.ui.label(root, "AAAAAAAAAAAA")
  scream:style({text_font = 24, text_color = 0xffffff})
  scream:align("center", 0, 0)
  scream:hidden(true)

  local now = badge.sys.ms()
  still_since, cand_since = now, now
  show_tally()
  set_mood("calm", now)
end

local function start_fall(now)
  falling, fall_start = true, low_since
  scream_bg:hidden(false)
  scream:hidden(false)
end

local function land(now)
  falling, low_since = false, nil
  scream_bg:hidden(true)
  scream:hidden(true)
  local t = now - fall_start
  drops = drops + 1
  badge.store.set_int("drops", drops)
  show_tally()
  hold("landed", string.format("@ DROPPED ME. Fell ~%d cm. That's drop #%d.",
    math.max(1, (490 * t * t) // 1000000), drops), 7000, now)
  fast, slow, still_since = 0, 0, now
end

function on_tick()
  local now = badge.sys.ms()
  local x, y, z = badge.sensor.accel()
  if not x then
    if have_accel then have_accel = false; say("My inner ear is broken. (no accel)") end
    return
  end
  have_accel = true
  local mag = math.sqrt(x * x + y * y + z * z)

  if mag < FALL_MG then
    low_since = low_since or now
    if not falling and now - low_since >= FALL_MS then start_fall(now) end
  else
    if falling then land(now) end
    low_since = nil
  end

  if not falling then
    -- This sensor rests near 1100 mg, not 1000: measure wobble around a learned 1 g.
    base = base or mag
    base = base + (mag - base) * 0.01
    local d = math.abs(mag - base)
    fast = fast + (d - fast) * 0.2
    slow = slow + (d - slow) * 0.02

    if badge.sensor.shake() then
      shakes = shakes + 1; dirty = true
      show_tally()
      shake_times[#shake_times + 1] = now
      while shake_times[1] and now - shake_times[1] > 7000 do table.remove(shake_times, 1) end
      if override_mood ~= "landed" then
        if #shake_times >= 3 then
          hold("dizzy", pick(QUIPS.dizzy), 6000, now)
        elseif override_mood ~= "dizzy" then
          hold("shake", pick(QUIPS.shake), 3500, now)
        end
      end
    elseif badge.sensor.tap() and slow < FIDGET_MG and not override_mood then
      pokes = pokes + 1; dirty = true
      show_tally()
      if pokes % 3 == 0 then gossip(now) else hold("poke", pick(QUIPS.poke), 2500, now) end
    end

    if override_mood and now >= override_until then
      override_mood = nil
      name_l:align("top_mid", 0, 62)
      set_mood(mood, now)
    end

    local c = classify(now)
    if c ~= cand then cand, cand_since = c, now end
    if cand ~= mood and now - cand_since >= SWITCH_MS then
      mood = cand
      if not override_mood then set_mood(mood, now) end
    elseif not override_mood and now >= next_rotate then
      describe(now)
      next_rotate = now + ROTATE_MS
    end

    if override_mood == "dizzy" then
      name_l:align("top_mid", math.floor(9 * math.sin(now / 90)), 62 + math.floor(4 * math.sin(now / 130)))
    end
  end

  if now >= next_led then
    next_led = now + 50
    leds(now)
  end
  if debug_on and now >= next_dbg then
    next_dbg = now + 250
    stat:set_text(string.format("mag %d  fast %d  slow %d  %s",
      math.floor(mag), math.floor(fast), math.floor(slow), badge.sensor.orientation()))
  end
end

function on_button(button, kind)
  if kind ~= K.PRESSED or falling then return end
  local now = badge.sys.ms()
  if button == B.A then gossip(now)
  elseif button == B.B then rap_sheet(now)
  elseif button == B.START then
    debug_on = not debug_on
    show_tally()
  end
end

function on_exit()
  if dirty then
    badge.store.set_int("shakes", shakes)
    badge.store.set_int("pokes", pokes)
    badge.store.set_int("nap", nap_best)
  end
  badge.led.clear()
  badge.led.show()
end
