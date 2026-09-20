local HELPER = noctalia.pluginDir() .. "/carla-osc.py"

function onEnable()
  noctalia.runStream("python3 " .. HELPER .. " --daemon", function(line)
    noctalia.log(line)
  end)
end

function update()
  noctalia.setUpdateInterval(30000)
end
