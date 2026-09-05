import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.familygraph.app',
  appName: 'FamilyGraph',
  webDir: 'www',
  bundledWebRuntime: false,
  android: {
    backgroundColor: '#090a16',
  },
};

export default config;
