-- Pandoc Lua filter: run a command and embed its stdout as an image.
-- Usage: ```{.run cmd="uv run irr_plot.py"}
--        ```
-- The command must print a path to an image file on stdout.
function CodeBlock(block)
  if block.classes[1] ~= "run" then return nil end
  local cmd = block.attributes["cmd"]
  if not cmd then return nil end
  local handle = io.popen(cmd .. " 2>/dev/null")
  local outfile = handle:read("*l")
  handle:close()
  if not outfile or outfile == "" then return nil end
  return pandoc.Para({pandoc.Image("", outfile)})
end
