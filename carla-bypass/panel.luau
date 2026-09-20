local PLUGIN_DIR = noctalia.pluginDir()
local HELPER = PLUGIN_DIR .. "/carla-osc.py"
local STATE = "/tmp/noctalia-carla-bypass.json"

local plugins = {}
local lastState = ""

local function header()
  return ui.row({ align = "center", gap = 6 }, {
    ui.glyph({ name = "adjustments", size = 16, color = "primary" }),
    ui.label({ text = "Carla Plugins", fontSize = 14, fontWeight = "bold", flexGrow = 1 }),
    ui.button({
      glyph = "refresh",
      glyphSize = 14,
      variant = "ghost",
      tooltip = "Refresh",
      onClick = "refresh",
    }),
  })
end

local function entry(plugin)
  local dim = plugin.active and 1 or 0.5

  return ui.row({
    key = "plugin-" .. plugin.id,
    align = "center",
    gap = 8,
    paddingH = 10,
    paddingV = 6,
    radius = 8,
    fill = "surface_variant",
  }, {
    ui.glyph({
      name = "wave-sine",
      size = 16,
      color = plugin.active and "primary" or "on_surface",
      opacity = dim,
    }),
    ui.label({
      text = plugin.name,
      fontSize = 13,
      maxLines = 1,
      flexGrow = 1,
      opacity = dim,
    }),
    ui.toggle({
      checked = plugin.active,
      onChange = function(value)
        togglePlugin(plugin.id, value == "true")
      end,
    }),
  })
end

local function render()
  local rows = {}

  if #plugins == 0 then
    table.insert(rows, ui.glyph({
      name = "mood-sad-squint",
      size = 48,
      color = "#FF21008c"
    }))
    table.insert(rows, ui.label({
      key = "empty",
      text = "There is no plugins to show at this moment, make sure Carla OSC is enabled on port 22752",
      textAlign = "center",
      fontSize = 13,
      opacity = 0.8,
    }))
  else
    for _, plugin in ipairs(plugins) do
      table.insert(rows, entry(plugin))
    end
  end

  panel.render(ui.column({ gap = 6, padding = 10 }, {
    header(),
    ui.scroll({ key = "list-" .. #plugins, gap = 6, flexGrow = 1 }, rows),
  }))
end

local function parseState(data)
  local result = {}

  for id, name, active in data:gmatch('"id":(%d+),"name":"(.-)","active":(%a+)') do
    table.insert(result, {
      id = tonumber(id),
      active = active == "true",
      name = name,
    })
  end

  table.sort(result, function(a, b)
    return a.id < b.id
  end)

  return result
end

local function load(force)
  noctalia.readFileAsync(STATE, function(data)
    if not data or data == "" then
      return
    end
    if not force and data == lastState then
      return
    end

    lastState = data
    plugins = parseState(data)
    render()
  end)
end

function update()
  noctalia.setUpdateInterval(500)
  load(false)
end

function refresh()
  noctalia.runAsync({ "python3", HELPER, "--refresh" })
end

function togglePlugin(id, active)
  for _, plugin in ipairs(plugins) do
    if plugin.id == id then
      plugin.active = active
    end
  end
  render()

  noctalia.runAsync({
    "python3",
    HELPER,
    "--set-active",
    tostring(id),
    active and "1" or "0",
  })
end

function onOpen()
  panel.setWantsSecondTicks(true)
  load(true)
end
