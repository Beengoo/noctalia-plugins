local PANEL_ID = "beengoo/carla-bypass:panel"

function update()
  barWidget.render(ui.glyph({ name = "adjustments", size = 16 }))
end

function onClick()
  noctalia.togglePanel(PANEL_ID)
end
