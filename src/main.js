/**
 * Frontend for LoL Champ Select Advisor Tauri overlay
 * Polls backend API and displays recommendations
 */
import { listen } from '@tauri-apps/api/event';
import { invoke } from '@tauri-apps/api/tauri';

// DOM elements
const statusEl = document.getElementById('status');
const roleEl = document.getElementById('role');
const laneOpponentEl = document.getElementById('laneOpponent');
const recListEl = document.getElementById('recList');
const debugEl = document.getElementById('debugInfo');
const timestampEl = document.getElementById('timestamp');
const containerEl = document.querySelector('.container');

// State
let isVisible = true;
let lastUpdateTime = 0;
const UPDATE_INTERVAL = 1000; // Poll every second
const API_URL = 'http://127.0.0.1:8765/recommendations';

/**
 * Format timestamp to HH:MM:SS
 */
function formatTimestamp(ms) {
  const date = new Date(ms);
  return date.toTimeString().slice(0, 8);
}

/**
 * Update the UI with new data
 */
async function updateUI() {
  try {
    const response = await fetch(API_URL);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();

    // Update timestamp
    timestampEl.textContent = formatTimestamp(Date.now());
    lastUpdateTime = Date.now();

    // Handle errors
    if (data.error) {
      statusEl.textContent = `Error: ${data.error}`;
      statusEl.style.color = 'var(--error-color)';
      roleEl.textContent = '-';
      laneOpponentEl.textContent = '-';
      recListEl.innerHTML = '<div class="rec-item">No data available</div>';
      return;
    }

    // Update status
    statusEl.textContent = 'Connected';
    statusEl.style.color = 'var(--success-color)';

    // Update role and lane opponent
    roleEl.textContent = data.role || '-';
    laneOpponentEl.textContent = data.lane_opponent || '-';

    // Update recommendations
    if (data.recommendations && data.recommendations.length > 0) {
      recListEl.innerHTML = '';
      data.recommendations.forEach((rec, index) => {
        const recDiv = document.createElement('div');
        recDiv.className = `rec-item rank-${Math.min(index + 1, 3)}`;

        // Determine score color
        let scoreClass = 'low';
        if (rec.score >= 55) scoreClass = 'high';
        else if (rec.score >= 50) scoreClass = 'medium';

        recDiv.innerHTML = `
          <div class="rec-name">${rec.name}</div>
          <div class="rec-score ${scoreClass}">${rec.score.toFixed(1)}%</div>
          <div class="rec-reasons">
            ${rec.reasons.map(r => `<span class="reason-tag">${r}</span>`).join('')}
          </div>
        `;
        recListEl.appendChild(recDiv);
      });
    } else {
      recListEl.innerHTML = '<div class="rec-item">No recommendations</div>';
    }

    // Show debug info if available
    if (data.debug) {
      debugEl.innerHTML = `
        Role: ${data.debug.role || '-'} |
        Lane: ${data.debug.enemy_lane || '-'} |
        Pool: ${data.debug.pool_size || 0} champs
      `.trim();
    } else {
      debugEl.textContent = '';
    }

    // Add updating animation
    containerEl.classList.add('updating');
    setTimeout(() => containerEl.classList.remove('updating'), 300);

  } catch (error) {
    console.error('Failed to fetch recommendations:', error);
    statusEl.textContent = 'Error fetching data';
    statusEl.style.color = 'var(--error-color)';
    roleEl.textContent = '-';
    laneOpponentEl.textContent = '-';
    recListEl.innerHTML = '<div class="rec-item">Connection error</div>';
  }
}

/**
 * Set up event listeners
 */
function setupListeners() {
  // Listen for visibility toggle from backend
  listen('toggle-visibility', (event) => {
    isVisible = !isVisible;
    if (isVisible) {
      invoke('show_window');
    } else {
      invoke('hide_window');
    }
  });

  // Listen for position updates
  listen('update-position', (event) => {
    // Position will be handled by backend
  });

  // Handle keydown for F12 (toggle)
  document.addEventListener('keydown', (e) => {
    if (e.key === 'F12') {
      e.preventDefault();
      invoke('toggle_visibility');
    }
  });

  // Handle click-through (ignore clicks on the window)
  window.addEventListener('click', (e) => {
    // Allow clicks to pass through to underlying windows
    // This is handled by Tauri window settings (click-through: true)
  });
}

/**
 * Initialize the app
 */
async function init() {
  // Initial update
  await updateUI();

  // Set up periodic updates
  setInterval(updateUI, UPDATE_INTERVAL);

  // Set up event listeners
  setupListeners();

  // Hide initially (will be shown by backend when needed)
  // invoke('hide_window');
}

// Start the app
init().catch(console.error);