// @ts-check
import { defineConfig } from 'astro/config';
import github from "@astrojs/github-pages";

export default defineConfig({
  output: 'static',
  base: '/sci-cluster/',
  adapter: github(),
});