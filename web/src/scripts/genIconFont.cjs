const fs = require('fs')
const axios = require('axios')
const path = require('path')

const targetDir = path.resolve(__dirname, '../styles/fonts')

if (!fs.existsSync(targetDir)) {
  fs.mkdirSync(targetDir)
}

function rm(p) {
  if (fs.existsSync(p)) {
    fs.unlinkSync(p)
  }
}

async function download(filename, url) {
  const res = await axios.get(url, { responseType: 'stream' })
  const targetFile = path.join(targetDir, filename)
  rm(targetFile)
  res.data.pipe(fs.createWriteStream(targetFile))
}

async function handleSymbol(url) {
  await download('iconfont.js', `http:${url}`)
  console.log(`download ${url} successfully`)
}

async function handleFontClass(url) {
  // get css content
  const res = await axios.get(`http:${url}`)
  let cssContent = res.data
  const regexp = /url\('(.*?)'\)/g
  let r,
    urls = []

  // get all fonts from css content
  while ((r = regexp.exec(cssContent))) {
    const url = r[1]
    if (!url.startsWith('//at.alicdn.com')) {
      continue
    }
    urls.push(url)
  }

  // download fonts
  await Promise.all(
    urls.map(async u => {
      const ext = /(\.(\w+)\?)|(\.(\w+)$)/.exec(u)[2]
      const hash = /#.*/g.exec(u)?.[0] || ''
      const query = /(\?t=\d*)#?/.exec(u)[1] || ''
      await download(`iconfont.${ext}`, `http:${u}`)
      console.log(`download ${u} successfully`)
      cssContent = cssContent.replace(u, `iconfont.${ext}${query}${hash}`)
    })
  )

  // fix css content font location
  const cssFilePath = path.join(targetDir, 'iconfont.css')
  rm(cssFilePath)
  fs.writeFileSync(cssFilePath, cssContent)
}

async function syncFonts(url) {
  if (url.endsWith('.css')) {
    await handleFontClass(url)
  } else if (url.endsWith('.js')) {
    await handleSymbol(url)
  } else {
    throw new Error('url should ends with .css or .js')
  }

  console.log('download all iconfont successfully!')
}

if (process.argv.length < 3) {
  throw new Error('please input you target font url')
}

syncFonts(process.argv[process.argv.length - 1])

// usage
// yarn sync:icon //at.alicdn.com/t/font_674880_7bgnky2kdxx.css
