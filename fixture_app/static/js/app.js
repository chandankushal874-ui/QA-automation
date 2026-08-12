/**
 * Fixture App — Main JS Bundle
 *
 * This file contains hidden API endpoint references that should be
 * discoverable by JS static analysis / endpoint mining.
 */

// Hidden endpoint references (not linked anywhere in the DOM)
const API_ENDPOINTS = {
    config: '/api/internal/config',
    users: '/api/internal/users',
    metrics: '/api/internal/metrics',
    graphql: '/graphql',
    quickLogin: '/api/auth/quick-login',
    debugError: '/debug/error',
    debugInfo: '/debug/info',
    slowEndpoint: '/api/slow',
    unstableEndpoint: '/api/unstable',
};

// WebSocket reference (for WebSocket endpoint detection)
const WS_URL = 'ws://localhost:8090/ws/notifications';

// Another hidden fetch call
async function checkSystemHealth() {
    const response = await fetch('/health');
    return response.json();
}

// GraphQL query reference
async function fetchUsersViaGraphQL() {
    const query = `
        query {
            users {
                id
                username
                email
                role
            }
        }
    `;
    const res = await fetch('/graphql', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
    });
    return res.json();
}

console.log('Fixture App JS loaded');
