-- Breaker Breaker: a CB-radio text walkie-talkie for badges running this app.
-- Chat: A type, B sends "10-4", START quick yaps, UP/DOWN channel, shake = rage.
-- Type: D-pad (or tilt) picks a key, A types, B deletes, AUX space, START sends.
-- Broadcasts your callsign (two words of your badge ID). Anyone on the channel can read it.

local B, K = badge.input.BUTTON, badge.input.KIND
local CHANNELS = {"General Yapping", "Free Food Intel", "Bug Support Group",
  "Swag Recon", "Secret (it isn't)"}
local QUICK = {"10-4 GOOD BUDDY", "BREAKER BREAKER, ANYONE GOT SNACKS?",
  "WHAT'S YOUR 20?", "MAYDAY. MERGE CONFLICT.", "DEMO IS ON FIRE (GOOD)",
  "DEMO IS ON FIRE (BAD)", "SEND CAFFEINE", "NEGATIVE, GHOST RIDER"}
local DEAD_AIR = {"Dead air. Just you and the void, good buddy.", "*static*",
  "Nobody out there. Try the snack table.", "Squelch is lonely tonight."}
local ROWS = {"1234567890", "QWERTYUIOP", "ASDFGHJKL'", "ZXCVBNM,.?"}
local ZONES = {{14, 70}, {112, 96}, {242, 60}}
local DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
local PART, MAX_DRAFT = 37, 90

local chat, typeb, quick, head, sub, log_l, draft_l, tfoot, cur, tilt_l, quick_l
local mode, ch, callsign, mytail = "chat", 1, "Ghost", "0000"
local radio_ok = false
local entries, draft = {}, ""
local row, col, tilt_on, qsel = 2, 1, false, 1
local txq, next_tx, seq, pending = {}, 0, 0, nil
local peers, rx, done = {}, {}, {}
local next_beacon, next_house, next_led, next_tilt, last_rx = 0, 0, 0, 0, 0
local fx, fx_until = nil, 0

local function say(text)
  entries[#entries + 1] = text:sub(1, 150)
  if #entries > 6 then table.remove(entries, 1) end
  log_l:set_text(table.concat(entries, "\n"))
end

local function on_air(now)
  local n = 0
  for _, p in pairs(peers) do if now - p.t < 30000 then n = n + 1 end end
  return n
end

local function header(now)
  head:set_text(string.format("CH%d  %s", ch, CHANNELS[ch]))
  sub:set_text(string.format("%s  |  %d on air", callsign, on_air(now)))
end

local function show(m)
  mode = m
  chat:hidden(m ~= "chat")
  typeb:hidden(m ~= "type")
  quick:hidden(m ~= "quick")
end

local function flash(kind, now, ms)
  fx, fx_until = kind, now + ms
end

local function transmit(text, now)
  if not radio_ok then say("Radio's busted, good buddy. Reboot and retry."); return end
  text = text .. " OVER"
  seq = (seq + 1) % 1296
  local id = DIGITS:sub(seq // 36 + 1, seq // 36 + 1) .. DIGITS:sub(seq % 36 + 1, seq % 36 + 1)
  local total = (#text + PART - 1) // PART
  local parts = {}
  for i = 1, total do
    parts[i] = "Wm" .. ch .. id .. i .. total .. text:sub((i - 1) * PART + 1, i * PART)
  end
  pending = {id = id, parts = parts, tries = 0, due = now}
  say(">> " .. text)
end

local function draw_draft()
  draft_l:set_text(draft .. "_")
  tfoot:set_text(string.format("A key  B del  AUX space  START send  %d/%d", #draft, MAX_DRAFT))
end

local function draw_cursor()
  if row <= 4 then
    cur:set_size(28, 28)
    cur:set_pos(1 + (col - 1) * 31, 58 + (row - 1) * 30)
  else
    local z = ZONES[col <= 3 and 1 or (col <= 7 and 2 or 3)]
    cur:set_size(z[2], 26)
    cur:set_pos(z[1], 180)
  end
end

local function draw_quick()
  local t = {}
  for i = 1, #QUICK do t[i] = (i == qsel and "> " or "  ") .. QUICK[i] end
  quick_l:set_text(table.concat(t, "\n"))
end

local function send_draft(now)
  if #draft == 0 then
    show("chat")
    say("Can't send silence. That's just regular radio.")
    return
  end
  transmit(draft, now)
  draft = ""
  draw_draft()
  show("chat")
end

local function press_key(now)
  if row <= 4 then
    if #draft < MAX_DRAFT then draft = draft .. ROWS[row]:sub(col, col) end
  elseif col <= 3 then
    if #draft < MAX_DRAFT then draft = draft .. " " end
  elseif col <= 7 then
    tilt_on = not tilt_on
    tilt_l:set_text(tilt_on and "TILT ON" or "TILT OFF")
  else
    send_draft(now); return
  end
  draw_draft()
end

local function move(dr, dc)
  row = (row - 1 + dr) % 5 + 1
  col = (col - 1 + dc) % 10 + 1
  draw_cursor()
end

local function tune(delta, now)
  ch = (ch - 1 + delta) % #CHANNELS + 1
  peers, rx, done, pending = {}, {}, {}, nil
  next_beacon = now
  header(now)
  say(string.format("-- CH%d: %s. Keep it clean. Or don't.", ch, CHANNELS[ch]))
end

local function received(mac, rssi, p)
  if #p < 3 or p:sub(1, 1) ~= "W" or p:sub(3, 3) ~= tostring(ch) then return end
  local now = badge.sys.ms()
  local kind = p:sub(2, 2)
  local tail = (mac:gsub(":", "")):sub(-4)
  local peer = peers[tail]
  if not peer then peer = {name = "Unit " .. tail}; peers[tail] = peer end
  peer.t = now
  if kind == "h" then
    local name = (p:sub(4, 19):gsub("[^ -~]", "?"))
    if peer.name ~= name then
      peer.name = name
      say("-- " .. name .. " rolled onto the channel.")
    end
    header(now)
  elseif kind == "a" then
    if pending and not pending.acked and p:sub(4, 5) == pending.id and p:sub(6, 9) == mytail then
      pending.acked = true
      say("-- " .. peer.name .. " copies. 10-4.")
    end
  elseif kind == "m" then
    local id, i, total = p:sub(4, 5), tonumber(p:sub(6, 6)), tonumber(p:sub(7, 7))
    if not i or not total or i < 1 or i > total or total > 3 then return end
    local key = tail .. id
    if not done[key] then
      local asm = rx[key]
      if not asm then asm = {n = 0, t = now, parts = {}}; rx[key] = asm end
      if not asm.parts[i] then asm.parts[i] = p:sub(8); asm.n = asm.n + 1 end
      if asm.n < total then return end
      rx[key], done[key] = nil, now
      local text = (table.concat(asm.parts):gsub("[^ -~]", "?"))
      rssi = rssi or -99
      local where = rssi > -50 and "close enough to smell" or
        (rssi > -72 and "nearby") or "faint. snack table?"
      say(string.format("%s: %s [%s]", peer.name, text, where))
      last_rx = now
      flash("rx", now, 700)
    end
    txq[#txq + 1] = "Wa" .. ch .. id .. tail
  end
end

local function key_label(parent, text, x, y)
  return badge.ui.label{parent = parent, text = text, x = x, y = y,
    text_font = 18, text_color = 0x7dff7d}
end

local function screen(root, hidden)
  return badge.ui.box{parent = root, x = 0, y = 0, w = 320, h = 240, hidden = hidden,
    bg_color = 0x0a0f0a, radius = 0, border_width = 0, pad_all = 0}
end

function on_enter(root)
  local now = badge.sys.ms()
  -- Bluetooth needs a big chunk of RAM: grab it before the UI eats any.
  -- Measured on fw v0.1.2-392: BLE init eats >50 KB and panics the badge
  -- ("BLE_INIT: Malloc failed") when it can't get it, so never try it short.
  collectgarbage()
  radio_ok = badge.sys.stats().free_heap >= 64000 and badge.radio.enable()
  chat = screen(root, false)
  head = badge.ui.label{parent = chat, text = "", x = 6, y = 3, text_font = 16, text_color = 0xffb000}
  sub = badge.ui.label{parent = chat, text = "", x = 6, y = 24, text_font = 14, text_color = 0x4fa04f}
  local logbox = badge.ui.box{parent = chat, x = 0, y = 44, w = 320, h = 172,
    bg_color = 0x050805, radius = 0, border_width = 0, pad_all = 4}
  log_l = badge.ui.label{parent = logbox, text = "", w = 308, text_font = 14, text_color = 0x7dff7d}
  log_l:align("bottom_left", 0, 0)
  badge.ui.label{parent = chat, text = "A type  B 10-4  START quick  UP/DN channel",
    x = 6, y = 221, text_font = 14, text_color = 0x4fa04f}

  typeb = screen(root, true)
  draft_l = badge.ui.label{parent = typeb, text = "_", x = 6, y = 4, w = 308,
    text_font = 16, text_color = 0xffb000}
  cur = badge.ui.box{parent = typeb, x = 0, y = 0, w = 28, h = 28,
    bg_color = 0x1f5a1f, radius = 4, border_width = 0}
  for r = 1, 4 do
    for c = 1, 10 do
      key_label(typeb, ROWS[r]:sub(c, c), 9 + (c - 1) * 31, 61 + (r - 1) * 30)
    end
  end
  key_label(typeb, "SPACE", 22, 183)
  tilt_l = key_label(typeb, "TILT OFF", 120, 183)
  key_label(typeb, "SEND", 250, 183)
  tfoot = badge.ui.label{parent = typeb, text = "", x = 6, y = 221, text_font = 14, text_color = 0x4fa04f}

  quick = screen(root, true)
  badge.ui.label{parent = quick, text = "QUICK YAPS", x = 6, y = 3, text_font = 16, text_color = 0xffb000}
  quick_l = badge.ui.label{parent = quick, text = "", x = 6, y = 30, w = 308,
    text_font = 16, text_color = 0x7dff7d}
  badge.ui.label{parent = quick, text = "UP/DN pick   A send   B back",
    x = 6, y = 221, text_font = 14, text_color = 0x4fa04f}

  local words = {}
  for w in (badge.me.badge_id() or ""):gmatch("[a-z]+") do
    words[#words + 1] = w:sub(1, 1):upper() .. w:sub(2)
  end
  if radio_ok then
    mytail = (badge.radio.mac():gsub(":", "")):sub(-4)
    badge.radio.on_recv(received)
  end
  callsign = #words >= 2 and (words[#words] .. " " .. words[#words - 1]) or ("Ghost " .. mytail)
  callsign = callsign:sub(1, 16)

  draw_draft(); draw_cursor(); draw_quick(); header(now)
  say("-- Breaker breaker. You are " .. callsign .. ".")
  say(radio_ok and "-- Open this app on another badge to yap." or
    "-- No RAM for the radio on this firmware. Antenna's a coat hanger.")
  last_rx, next_beacon = now, now + 500
end

local function leds(now)
  badge.led.clear()
  if fx and now < fx_until then
    local left = (fx_until - now)
    if fx == "tx" then
      local step = 3 - math.min(2, left // 140)
      local l, r = ({5, 6, 1})[step], ({4, 3, 2})[step]
      badge.led.set(l, 255, 30, 0); badge.led.set(r, 255, 30, 0)
    else
      badge.led.set_all(0, math.min(255, left // 3 + 20), 0)
    end
  else
    fx = nil
    local wave = (now % 3000) / 1500
    if wave > 1 then wave = 2 - wave end
    badge.led.set(1, math.floor(30 + 60 * wave), math.floor(15 + 30 * wave), 0)
  end
  badge.led.show()
end

function on_tick()
  local now = badge.sys.ms()

  if pending and now >= pending.due then
    if pending.acked then
      pending = nil
    elseif pending.tries >= 3 then
      pending = nil
      say("-- No copy. You're yelling into the void.")
    else
      for i = 1, #pending.parts do txq[#txq + 1] = pending.parts[i] end
      pending.tries, pending.due = pending.tries + 1, now + 1200
      flash("tx", now, 420)
    end
  end
  if radio_ok and #txq > 0 and now >= next_tx then
    badge.radio.send(table.remove(txq, 1))
    next_tx = now + 130
  end
  if radio_ok and now >= next_beacon then
    next_beacon = now + 8000
    txq[#txq + 1] = "Wh" .. ch .. callsign
  end

  if now >= next_house then
    next_house = now + 2000
    for k, a in pairs(rx) do if now - a.t > 10000 then rx[k] = nil end end
    for k, t in pairs(done) do if now - t > 20000 then done[k] = nil end end
    if mode == "chat" then
      header(now)
      if now - last_rx > 45000 then
        last_rx = now
        say("-- " .. DEAD_AIR[badge.sys.random(#DEAD_AIR) + 1])
      end
    end
  end

  if badge.sensor.shake() and mode == "chat" and not pending then
    transmit("*SHAKES BADGE ANGRILY*", now)
  end

  if tilt_on and mode == "type" and now >= next_tilt then
    next_tilt = now + 220
    local x, y = badge.sensor.accel()
    if x then
      local dc = (x < -230 and 1) or (x > 230 and -1) or 0
      local dr = (y > 230 and 1) or (y < -230 and -1) or 0
      if dc ~= 0 or dr ~= 0 then move(dr, dc) end
    end
  end

  if now >= next_led then
    next_led = now + 50
    leds(now)
  end
end

function on_button(button, kind)
  if kind ~= K.PRESSED then return end
  local now = badge.sys.ms()
  if mode == "chat" then
    if button == B.A then show("type")
    elseif button == B.B then transmit("10-4", now)
    elseif button == B.START then show("quick")
    elseif button == B.UP then tune(1, now)
    elseif button == B.DOWN then tune(-1, now) end
  elseif mode == "type" then
    if button == B.A then press_key(now)
    elseif button == B.B then
      if #draft == 0 then show("chat") else draft = draft:sub(1, -2); draw_draft() end
    elseif button == B.AUX1 then
      if #draft < MAX_DRAFT then draft = draft .. " "; draw_draft() end
    elseif button == B.START then send_draft(now)
    elseif button == B.UP then move(-1, 0)
    elseif button == B.DOWN then move(1, 0)
    elseif button == B.LEFT then move(0, -1)
    elseif button == B.RIGHT then move(0, 1) end
  else
    if button == B.UP then qsel = (qsel - 2) % #QUICK + 1; draw_quick()
    elseif button == B.DOWN then qsel = qsel % #QUICK + 1; draw_quick()
    elseif button == B.A then transmit(QUICK[qsel], now); show("chat")
    elseif button == B.B then show("chat") end
  end
end

function on_exit()
  if radio_ok then
    badge.radio.on_recv(nil)
    badge.radio.disable()
  end
  badge.led.clear()
  badge.led.show()
end
