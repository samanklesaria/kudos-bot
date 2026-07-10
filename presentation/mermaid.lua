-- Pandoc Lua filter: render ```mermaid blocks to images via mmdc (cached)
local cachedir = os.getenv("MERMAID_CACHE") or "/tmp/mermaid-cache"
os.execute("mkdir -p " .. cachedir)

function CodeBlock(block)
  if block.classes[1] ~= "mermaid" then return nil end
  local hash = pandoc.utils.sha1(block.text)
  local outfile = cachedir .. "/" .. hash .. ".png"
  local f = io.open(outfile, "r")
  if f then
    f:close()
  else
    local infile = os.tmpname() .. ".mmd"
    f = io.open(infile, "w")
    f:write(block.text)
    f:close()
    os.execute("mmdc -i " .. infile .. " -o " .. outfile .. " -b transparent -w 800 -s 2 2>/dev/null")
    os.remove(infile)
  end
  return pandoc.Para({pandoc.Image("", outfile)})
end
