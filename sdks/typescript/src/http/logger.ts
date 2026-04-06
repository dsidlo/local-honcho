import fs from 'fs'

const LOG_FILE = '/tmp/honcho-pi.log'

export function logError(message: string, error?: Error) {
  const timestamp = new Date().toISOString()
  const entry = `[${timestamp}] ERROR: ${message}`
  if (error) {
    entry += `\\nStack: ${error.stack || error.message}\\n`
  }
  fs.appendFileSync(LOG_FILE, entry + '\\n')
}

export function logDebug(message: string) {
  const timestamp = new Date().toISOString()
  const entry = `[${timestamp}] DEBUG: ${message}\\n`
  fs.appendFileSync(LOG_FILE, entry)
}
