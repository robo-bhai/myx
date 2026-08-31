// --- PUSH EVENT: Listen for incoming notifications ---
self.addEventListener('push', (event) => {
  let data = { 
    title: 'Hadi88 Premium', 
    body: 'New alert from the panel!',
    url: '/dashboard' 
  };
  
  if (event.data) {
    try {
      // Try to parse JSON from the push server
      data = event.data.json();
    } catch (e) {
      // Fallback to plain text if JSON fails
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body,
    // The main branding icon
    icon: '/static/Icons/Icon-192x192.png',
    // The status bar icon
    badge: '/static/Icons/Icon-192x192.png',
    // Modern vibration pattern for better UX
    vibrate: [300, 100, 300],
    // High-priority for instant arrival
    priority: 'high',
    data: {
      // Directs specifically to dashboard or provided URL
      url: data.url || '/dashboard' 
    },
    // Modern Action Button
    actions: [
      { action: 'open_url', title: 'Open Dashboard' }
    ]
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// --- NOTIFICATION CLICK: Open /dashboard ---
self.addEventListener('notificationclick', (event) => {
  // Close the notification immediately
  event.notification.close();

  // Get the target URL from data, default to dashboard
  const targetUrl = new URL(event.notification.data.url || '/dashboard', self.location.origin).href;

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      // If a dashboard window is already open, focus it
      for (let client of windowClients) {
        if (client.url === targetUrl && 'focus' in client) {
          return client.focus();
        }
      }
      // Otherwise, open a new window at /dashboard
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
