-- Pandoc Lua filter: render ```mermaid blocks to images via mmdc
local counter = 0
function CodeBlock(block)
  if block.classes[1] ~= "mermaid" then return nil end
  counter = counter + 1
  local infile = os.tmpname() .. ".mmd"
  local outfile = os.tmpname() .. ".png"
  local f = io.open(infile, "w")
  f:write(block.text)
  f:close()
  os.execute("mmdc -i " .. infile .. " -o " .. outfile .. " -b white -w 800 -s 2 2>/dev/null")
  os.remove(infile)
  return pandoc.Para({pandoc.Image("", outfile)})
end
