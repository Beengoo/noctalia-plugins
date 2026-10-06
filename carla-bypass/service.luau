local HELPER = noctalia.pluginDir() .. "/carla-osc.py"

-- Top level, not onEnable(): onEnable() only fires on an explicit enable from
-- the plugin manager, never on ordinary Noctalia startup.
noctalia.runStream("python3 " .. HELPER .. " --daemon", function(line)
  noctalia.log(line)
end)

function update()
  noctalia.setUpdateInterval(30000)
end
