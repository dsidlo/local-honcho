// @ts-nocheck
// Standalone extension for /honcho-apply-learnings command
// Fixed: Log to file, proper async handling, prevents hang, accurate reporting

const LOG_FILE = '/tmp/honcho-apply-learnings.log';

// Simple file logger
async function log(level: 'info'|'warn'|'error', msg: string, data?: any) {
  const fs = await import('node:fs/promises');
  const timestamp = new Date().toISOString().slice(0, 19);
  const prefix = `[${timestamp}] [${level.toUpperCase()}]`;
  let line = `${prefix} ${msg}`;
  if (data !== undefined) {
    line += ' ' + (typeof data === 'object' ? JSON.stringify(data) : String(data));
  }
  line += '\n';

  try {
    await fs.appendFile(LOG_FILE, line);
  } catch (e) {
    // Silent fail - can't even log the error
  }
}

export default function (pi: any) {
  pi.registerCommand("honcho-apply-learnings", {
    description: "Apply recent Honcho synthesis learnings to teams agent prompts from settings.json",
    handler: async function(args: string, ctx: any) {
      // Fire-and-forget log init
      const fs = await import('node:fs/promises');
      await fs.writeFile(LOG_FILE, `=== Starting /honcho-apply-learnings at ${new Date().toISOString()} ===\n`);

      await log('info', 'Command started with args:', args);

      // Notify immediately so user knows we're working
      ctx.ui.notify('Starting apply-learnings...', 'info');

      try {
        // Step 1: Import modules
        await log('info', 'Step 1: Loading modules...');
        const { readFile, writeFile, access, appendFile } = await import('node:fs/promises');
        const { exec } = await import('node:child_process');
        const { promisify } = await import('node:util');
        const execAsync = promisify(exec);
        await log('info', 'Modules loaded');

        // Step 2: Get git branch
        await log('info', 'Step 2: Getting git branch...');
        let branch = 'unknown';
        try {
          const { stdout } = await execAsync('git branch --show-current 2>/dev/null || echo "unknown"', { timeout: 5000 });
          branch = stdout.trim();
          await log('info', 'Branch:', branch);
        } catch (e: any) {
          await log('warn', 'Git branch failed:', e.message);
        }

        // Step 3: Load settings
        await log('info', 'Step 3: Loading settings...');
        const settingsPath = '/home/dsidlo/.pi/agent/settings.json';
        let settings: any;
        let configSource = 'file';

        try {
          const content = await readFile(settingsPath, 'utf-8');
          await log('info', 'Settings file read, size:', content.length);
          settings = JSON.parse(content);
          await log('info', 'Settings parsed');
        } catch (e: any) {
          await log('error', 'Settings parse failed:', e.message);
          settings = {
            honcho: {
              applyLearnings: {
                enabled: true,
                agents: ['teams-manager', 'teams-architect', 'teams-developer', 'teams-reviewer'],
                autoApply: false
              }
            }
          };
          configSource = 'fallback';
          await log('info', 'Using fallback config');
        }

        const applyConfig = settings.honcho?.applyLearnings || { agents: [], enabled: false };
        await log('info', 'Config source:', configSource);
        await log('info', 'Apply config:', { enabled: applyConfig.enabled, agentCount: applyConfig.agents?.length });

        if (!applyConfig.enabled) {
          await log('warn', 'Config disabled, aborting');
          ctx.ui.notify('Apply learnings disabled in settings.json', 'warning');
          await log('info', 'Command complete (disabled)');
          return { success: false, reason: 'disabled' };
        }

        // Step 4: Determine target agents
        await log('info', 'Step 4: Processing args...');
        let targetAgents: string[] = applyConfig.agents || [];
        const arg = (args || '').trim().toLowerCase();
        await log('info', 'Raw arg:', arg);
        await log('info', 'Initial agents:', targetAgents);

        if (arg === 'all') {
          targetAgents = ['teams-manager', 'teams-architect', 'teams-developer', 'teams-reviewer'];
          await log('info', 'Using ALL agents');
        } else if (arg && targetAgents.includes(arg)) {
          targetAgents = [arg];
          await log('info', 'Filtered to single agent:', arg);
        } else if (arg) {
          await log('error', 'Invalid agent:', arg);
          ctx.ui.notify(`Agent '${arg}' not configured`, 'error');
          return { success: false, reason: 'invalid_agent' };
        }

        if (targetAgents.length === 0) {
          await log('error', 'No agents configured');
          ctx.ui.notify('No agents configured', 'error');
          return { success: false, reason: 'no_agents' };
        }

        await log('info', 'Final targets:', targetAgents);
        ctx.ui.notify(`Processing ${targetAgents.length} agent(s)...`, 'info');

        // Step 5: Get data
        const timestamp = new Date().toISOString().slice(0, 16).replace('T', ' ');
        await log('info', 'Timestamp:', timestamp);

        let learnings: string[] = [];

        // Try API
        await log('info', 'Step 5: Trying API...');
        try {
          const HONCHO_URL = 'http://localhost:8000/v3';
          await log('info', 'API URL:', HONCHO_URL);

          // Ensure workspace (ignore errors)
          try {
            await fetch(`${HONCHO_URL}/workspaces`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id: 'default' })
            });
            await log('info', 'Workspace check done');
          } catch (e) {
            // Ignore
          }

          const response = await fetch(`${HONCHO_URL}/workspaces/default/conclusions/query`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              query: 'synthesis insights',
              top_k: 20,
              filters: { level: 'synthesis' }
            })
          });

          await log('info', 'API response status:', response.status);

          if (response.ok) {
            const result = await response.json();
            if (Array.isArray(result)) {
              learnings = result.map((d: any) => d.content).filter(Boolean);
              await log('info', 'API learnings loaded:', learnings.length);
            }
          } else {
            await log('warn', 'API not ok:', response.status);
          }
        } catch (e: any) {
          await log('error', 'API failed:', e.message);
        }

        // Try local files if API empty
        if (learnings.length === 0) {
          await log('info', 'Step 6: Trying local files...');
          const localFiles = [
            '/home/dsidlo/.local/lib/honcho/docs/v3/guides/community/dgs-integrations/synthesis-report-2026-03-28.md',
            '/home/dsidlo/.local/lib/honcho/docs/v3/guides/community/dgs-integrations/Honcho-Dream-Analysis.md'
          ];

          for (const filePath of localFiles) {
            try {
              await access(filePath);
              const content = await readFile(filePath, 'utf-8');
              learnings.push(content.substring(0, 5000));
              await log('info', 'Loaded local file:', filePath);
            } catch (e: any) {
              await log('warn', 'Local file failed:', filePath, e.message);
            }
          }
        }

        await log('info', 'Total learnings:', learnings.length);

        if (learnings.length === 0) {
          await log('error', 'No learnings from any source');
          ctx.ui.notify('No learnings available', 'warning');
          return { success: false, reason: 'no_learnings' };
        }

        // Step 7: Process agents
        await log('info', 'Step 7: Processing agents...');
        const insightsText = learnings.join('\n---\n');
        await log('info', 'Insights text length:', insightsText.length);

        let updatedCount = 0;
        let skippedCount = 0;
        const failures: string[] = [];

        for (const agentName of targetAgents) {
          await log('info', 'Processing:', agentName);

          const agentPath = `/home/dsidlo/.pi/agent/agents/${agentName}.md`;
          await log('info', 'Path:', agentPath);

          // Check exists
          try {
            await access(agentPath);
            await log('info', 'File exists: yes');
          } catch (e) {
            await log('error', 'File not found:', agentPath);
            failures.push(`${agentName}: not found`);
            continue;
          }

          try {
            // Read content
            const currentContent = await readFile(agentPath, 'utf-8');
            await log('info', 'Read file, size:', currentContent.length);

            // Check already applied
            if (currentContent.includes('Recent Synthesis Insights')) {
              await log('info', 'Already applied, skipping');
              skippedCount++;
              continue;
            }

            // Get old timestamp
            const match = currentContent.match(/Last-Updated:\s*([\d\s:-]+)/);
            const oldTimestamp = match ? match[1].trim() : 'unknown';
            await log('info', 'Old timestamp:', oldTimestamp);

            // Build new section
            let tailored = insightsText;
            if (agentName.includes('manager')) tailored += '\n- Manager Focus';
            if (agentName.includes('architect')) tailored += '\n- Architect Focus';
            if (agentName.includes('developer')) tailored += '\n- Developer Focus';
            if (agentName.includes('reviewer')) tailored += '\n- Reviewer Focus';

            const section = `\n\n## Recent Synthesis Insights (Post 2026-03-28)\n\n${tailored.substring(0, 3000)}...\n\n### Resolution\n- Updated: ${oldTimestamp} -> ${timestamp}`;

            // Update header
            const newContent = currentContent.replace(/Last-Updated:\s*[^\n]*/g, `Last-Updated: ${timestamp}`) + section;
            await log('info', 'New content size:', newContent.length);

            // Write
            await writeFile(agentPath, newContent, 'utf-8');
            await log('info', 'Write successful');

            updatedCount++;
            await log('info', 'Agent complete:', agentName);

          } catch (e: any) {
            await log('error', 'Process failed:', agentName, e.message);
            failures.push(`${agentName}: ${e.message}`);
          }
        }

        // Step 8: Summary
        await log('info', '=== SUMMARY ===');
        await log('info', 'Updated:', updatedCount);
        await log('info', 'Skipped (already present):', skippedCount);
        await log('info', 'Failures:', failures.length);
        await log('info', 'Failure list:', failures);

        let msg: string;
        if (updatedCount === 0 && skippedCount === targetAgents.length && failures.length === 0) {
          msg = `No files updated - all ${skippedCount} agent(s) already have synthesis insights.`;
        } else {
          msg = `Updated ${updatedCount}/${targetAgents.length} agents.`;
          if (skippedCount > 0) msg += ` Skipped ${skippedCount} (already present).`;
          if (failures.length > 0) msg += ` Failed: ${failures.join(', ')}.`;
        }

        await log('info', 'Final message:', msg);
        ctx.ui.notify(msg, failures.length === 0 ? 'info' : 'warning');
        await log('info', '=== COMMAND COMPLETE ===');

        return { success: true, updated: updatedCount, skipped: skippedCount, failed: failures.length };

      } catch (error: any) {
        await log('error', 'FATAL:', error.message);
        await log('error', 'Stack:', error.stack);
        ctx.ui.notify(`Failed: ${error.message}`, 'error');
        await log('info', '=== COMMAND FAILED ===');
        return { success: false, error: error.message };
      }
    }
  });
}
